"""Live Docker isolation tests. Run only in the dedicated CI job with Docker available."""
import os
import subprocess
from pathlib import Path

import pytest

from sandbox_runner import server


pytestmark = pytest.mark.skipif(
    os.getenv("OPENMANUS_SANDBOX_INTEGRATION") != "1",
    reason="requires a live Docker daemon and the built sandbox image",
)


@pytest.fixture
def sandbox_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(server, "TOKEN", "integration-test-token")
    monkeypatch.setattr(server, "PROJECTS_ROOT", root.resolve())
    monkeypatch.setattr(server, "IMAGE", "openmanus-code-runner:latest")
    return root


def run_project(root: Path, name: str, command: list[str], timeout: int = 10):
    project = root / name
    project.mkdir()
    return server.execute_job({
        "project": name,
        "command": command,
        "timeout_seconds": timeout,
    })


def test_container_reads_only_selected_project(sandbox_root, tmp_path):
    (sandbox_root / "one").mkdir()
    (sandbox_root / "one" / "marker.txt").write_text("project-data", encoding="utf-8")
    outside = tmp_path / "host-secret.txt"
    outside.write_text("host-secret-value", encoding="utf-8")
    (sandbox_root / "one" / ".env").write_text("API_KEY=project-secret", encoding="utf-8")
    project = sandbox_root / "one"
    result = server.execute_job({
        "project": "one",
        "command": [
            "python", "-c",
            "from pathlib import Path; "
            "print(Path('/workspace/marker.txt').read_text()); "
            f"print('HOST_SECRET_VISIBLE' if Path({str(outside)!r}).exists() else 'HOST_SECRET_BLOCKED')",
        ],
        "timeout_seconds": 10,
    })
    assert result["status"] == "passed", result["stderr"]
    assert "project-data" in result["stdout"]
    assert "HOST_SECRET_BLOCKED" in result["stdout"]
    assert "host-secret-value" not in result["stdout"]




def test_generated_job_runs_unprivileged_and_syncs_only_project_output(sandbox_root):
    result = run_project(
        sandbox_root, "writes",
        ["python", "-c",
         "import os; from pathlib import Path; "
         "print('UID=' + str(os.getuid())); "
         "print(next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:'))); "
         "Path('/workspace/generated.txt').write_text('created safely')"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert "UID=65534" in result["stdout"]
    assert "CapEff:\t0000000000000000" in result["stdout"]
    assert (sandbox_root / "writes" / "generated.txt").read_text(encoding="utf-8") == "created safely"



def test_project_environment_secrets_are_not_copied_into_job(sandbox_root):
    project = sandbox_root / "secrets"
    project.mkdir()
    (project / ".env").write_text("API_KEY=secret", encoding="utf-8")
    result = server.execute_job({
        "project": "secrets",
        "command": ["python", "-c", "from pathlib import Path; print(Path('/workspace/.env').exists())"],
        "timeout_seconds": 10,
    })
    assert result["status"] == "passed", result["stderr"]
    assert result["stdout"].strip() == "False"

def test_container_has_no_network(sandbox_root):
    result = run_project(
        sandbox_root, "network",
        ["python", "-c",
         "import socket; s=socket.socket(); s.settimeout(1); "
         "try_connect=False\n"
         "try:\n s.connect(('1.1.1.1', 53)); try_connect=True\n"
         "except OSError: pass\n"
         "print('NETWORK_ALLOWED' if try_connect else 'NETWORK_BLOCKED')"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert "NETWORK_BLOCKED" in result["stdout"]
    assert "NETWORK_ALLOWED" not in result["stdout"]


def test_container_root_filesystem_is_read_only(sandbox_root):
    result = run_project(
        sandbox_root, "readonly",
        ["python", "-c", "open('/openmanus-security-test', 'w').write('bad')"],
    )
    assert result["status"] == "failed"
    assert "Read-only file system" in result["stderr"] or "PermissionError" in result["stderr"]


def test_output_capture_is_bounded(sandbox_root):
    result = run_project(
        sandbox_root, "output",
        ["python", "-c", "print('x' * 500000)"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert len(result["stdout"].encode("utf-8")) <= server.MAX_OUTPUT




def test_descendants_are_killed_before_workspace_sync(sandbox_root):
    project = sandbox_root / "descendants"
    project.mkdir()
    child_code = "import time; time.sleep(2); open('/workspace/descendant.txt','w').write('survived')"
    result = server.execute_job({
        "project": "descendants",
        "command": [
            "python", "-c",
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c',sys.argv[1]], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print('parent-exit')",
            child_code,
        ],
        "timeout_seconds": 10,
    })
    assert result["status"] == "passed", result["stderr"]
    assert "parent-exit" in result["stdout"]
    assert not (project / "descendant.txt").exists()





def test_resource_limits_are_visible_inside_sandbox(sandbox_root):
    code = (
        "from pathlib import Path; "
        "print('CPU_MAX=' + Path('/sys/fs/cgroup/cpu.max').read_text().strip()); "
        "print('MEMORY_MAX=' + Path('/sys/fs/cgroup/memory.max').read_text().strip()); "
        "print('PIDS_MAX=' + Path('/sys/fs/cgroup/pids.max').read_text().strip())"
    )
    result = run_project(sandbox_root, "cgroup-limits", ["python", "-c", code])
    assert result["status"] == "passed"
    assert "CPU_MAX=100000 100000" in result["stdout"]
    assert "MEMORY_MAX=536870912" in result["stdout"]
    assert "PIDS_MAX=128" in result["stdout"]


def test_memory_limit_prevents_large_allocation(sandbox_root):
    result = run_project(
        sandbox_root, "memory-limit",
        ["python", "-c", "bytearray(700 * 1024 * 1024); print('ALLOCATED')"],
        timeout=10,
    )
    assert result["status"] != "passed"
    assert "ALLOCATED" not in result["stdout"]


def test_pid_limit_stops_process_fanout(sandbox_root):
    code = (
        "import subprocess; p=[]; "
        "forbidden=False\n"
        "for _ in range(200):\n"
        " try: p.append(subprocess.Popen(['/bin/sleep','30'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))\n"
        " except OSError: forbidden=True; print('PID_LIMIT_REACHED', len(p)); break\n"
        "print('SPAWNED', len(p))"
    )
    result = run_project(sandbox_root, "pid-limit", ["python", "-c", code], timeout=10)
    assert result["status"] in {"passed", "failed"}
    assert "PID_LIMIT_REACHED" in result["stdout"]
    assert "SPAWNED 200" not in result["stdout"]
    if result["status"] == "failed":
        assert "output discarded" in result["stderr"]
    inspect = subprocess.run(
        ["docker", "inspect", result["job_id"]],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    assert inspect.returncode != 0, "PID exhaustion must still remove the sandbox container"

def test_isolated_preview_uses_loopback_proxy_and_cleans_up(sandbox_root):
    import urllib.error
    import urllib.request

    project = sandbox_root / "preview"
    project.mkdir()
    (project / "index.html").write_text("<h1>Sandbox preview</h1>", encoding="utf-8")
    preview = server.start_preview({
        "project": "preview",
        "command": ["python", "-m", "http.server", "8000", "--bind", "0.0.0.0"],
        "port": 8000,
    })
    try:
        assert preview["url"].startswith("http://127.0.0.1:")
        with urllib.request.urlopen(preview["url"], timeout=5) as response:
            body = response.read().decode("utf-8")
        assert "Sandbox preview" in body
        assert server.preview_status(preview["preview_id"])["status"] == "running"
    finally:
        stopped = server.stop_preview(preview["preview_id"])
    assert stopped["status"] == "stopped"
    with pytest.raises((urllib.error.URLError, TimeoutError, OSError)):
        urllib.request.urlopen(preview["url"], timeout=1)

def test_timeout_kills_job_and_removes_container(sandbox_root):
    result = run_project(sandbox_root, "timeout", ["python", "-c", "import time; time.sleep(10)"], timeout=1)
    assert result["status"] == "timed_out"
    inspect = __import__("subprocess").run(
        ["docker", "ps", "-a", "--filter", "name=" + result["job_id"], "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result["job_id"] not in inspect.stdout



def test_container_has_no_linux_capabilities(sandbox_root):
    result = run_project(
        sandbox_root, "capabilities",
        ["python", "-c",
         "from pathlib import Path; "
         "status=Path('/proc/self/status').read_text(); "
         "caps=next(line.split()[1] for line in status.splitlines() if line.startswith('CapEff:')); "
         "print('CAP_EFF=' + caps)"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert "CAP_EFF=0000000000000000" in result["stdout"]


def test_job_cannot_access_runner_workspace_root(sandbox_root):
    result = run_project(
        sandbox_root, "runner-root",
        ["python", "-c",
         "from pathlib import Path; "
         "root=Path(" + repr(str(sandbox_root)) + "); "
         "print('RUNNER_ROOT_VISIBLE' if root.exists() else 'RUNNER_ROOT_HIDDEN')"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert "RUNNER_ROOT_HIDDEN" in result["stdout"]
    assert "RUNNER_ROOT_VISIBLE" not in result["stdout"]

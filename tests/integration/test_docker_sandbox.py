"""Live Docker isolation tests. Run only in the dedicated CI job with Docker available."""
import os
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
         "Path('/workspace/generated.txt').write_text('created safely')"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert "UID=65534" in result["stdout"]
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

def test_timeout_kills_job_and_removes_container(sandbox_root):
    result = run_project(sandbox_root, "timeout", ["python", "-c", "import time; time.sleep(10)"], timeout=1)
    assert result["status"] == "timed_out"
    inspect = __import__("subprocess").run(
        ["docker", "ps", "-a", "--filter", "name=" + result["job_id"], "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result["job_id"] not in inspect.stdout

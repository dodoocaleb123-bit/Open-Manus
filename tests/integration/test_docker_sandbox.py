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
        ["python", "-c",
         "from pathlib import Path; p=Path('/openmanus-security-test'); "
         "exec(\"try: p.write_text('bad')\\nexcept OSError: print('ROOTFS_READ_ONLY')\")"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert "ROOTFS_READ_ONLY" in result["stdout"]


def test_output_capture_is_bounded(sandbox_root):
    result = run_project(
        sandbox_root, "output",
        ["python", "-c", "print('x' * 500000)"],
    )
    assert result["status"] == "passed", result["stderr"]
    assert len(result["stdout"].encode("utf-8")) <= server.MAX_OUTPUT


def test_timeout_kills_job_and_removes_container(sandbox_root):
    result = run_project(sandbox_root, "timeout", ["python", "-c", "import time; time.sleep(10)"], timeout=1)
    assert result["status"] == "timed_out"
    inspect = __import__("subprocess").run(
        ["docker", "ps", "-a", "--filter", "name=" + result["job_id"], "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert result["job_id"] not in inspect.stdout

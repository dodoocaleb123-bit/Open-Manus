"""Phase 3 app lifecycle tests: workspace, artifacts, previews, and fail-closed behavior."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from coding import CodingWorkspace, CodingWorkspaceError
from coding.runner_client import SandboxUnavailable


class FakeRunner:
    def __init__(self):
        self.jobs = []
        self.previews = {}
        self.stopped = []
        self.next_preview = 1
        self.job_result = {
            "returncode": 0,
            "stdout": "2 passed",
            "stderr": "",
            "duration_seconds": 0.02,
            "status": "completed",
        }

    def run(self, payload):
        self.jobs.append(dict(payload))
        return dict(self.job_result)

    def start_preview(self, payload):
        preview_id = f"preview-{self.next_preview}"
        self.next_preview += 1
        result = {
            "preview_id": preview_id,
            "project": payload["project"],
            "status": "running",
            "port": 18765,
            "container_port": payload["port"],
            "url": "http://127.0.0.1:18765",
            "screenshot_url": "http://127.0.0.1:18765",
        }
        self.previews[preview_id] = {**result, "command": payload["command"]}
        return result

    def preview_status(self, preview_id):
        if preview_id not in self.previews:
            raise SandboxUnavailable("preview not found")
        return {key: self.previews[preview_id][key] for key in ("preview_id", "project", "status", "port", "url")}

    def preview_logs(self, preview_id):
        if preview_id not in self.previews:
            raise SandboxUnavailable("preview not found")
        return {"preview_id": preview_id, "status": "running", "output": "server ready"}

    def stop_preview(self, preview_id):
        self.stopped.append(preview_id)
        self.previews.pop(preview_id, None)
        return {"preview_id": preview_id, "status": "stopped"}


def make_workspace(tmp_path):
    runner = FakeRunner()
    workspace = CodingWorkspace(tmp_path / "projects", runner=runner)
    return workspace, runner


def test_app_build_lifecycle_create_edit_dependencies_test_build_repair(tmp_path):
    workspace, runner = make_workspace(tmp_path)
    workspace.create_project({"project": "sample-app"})
    workspace.write_file({"project": "sample-app", "path": "app.py", "content": "def answer():\n    return 42\n"})
    workspace.write_file({"project": "sample-app", "path": "test_app.py", "content": "def test_answer():\n    assert True\n"})

    assert "return 42" in workspace.read_file({"project": "sample-app", "path": "app.py"})["content"]
    assert workspace.run_tests({"project": "sample-app"})["returncode"] == 0
    assert workspace.run_build({"project": "sample-app", "command": ["npm", "run", "build"]})["status"] == "completed"

    runner.job_result = {"returncode": 1, "stdout": "", "stderr": "SyntaxError", "duration_seconds": 0.01, "status": "failed"}
    failed = workspace.run_tests({"project": "sample-app"})
    assert failed["returncode"] == 1 and "SyntaxError" in failed["stderr"]
    workspace.write_file({"project": "sample-app", "path": "app.py", "content": "def answer():\n    return 43\n"})
    runner.job_result = {"returncode": 0, "stdout": "2 passed", "stderr": "", "duration_seconds": 0.02, "status": "completed"}
    assert workspace.run_tests({"project": "sample-app"})["returncode"] == 0
    assert len(runner.jobs) == 4


def test_preview_start_status_logs_viewports_and_cleanup(tmp_path):
    workspace, runner = make_workspace(tmp_path)
    workspace.create_project({"project": "site"})
    workspace.write_file({"project": "site", "path": "index.html", "content": "<h1>Hello</h1>"})

    preview = workspace.start_preview({"project": "site", "port": 8080})
    assert preview["ready"] is True
    assert preview["viewports"]["desktop"] == {"width": 1440, "height": 900}
    assert preview["viewports"]["mobile"] == {"width": 390, "height": 844}
    assert runner.previews[preview["preview_id"]]["command"] == [
        "python", "-m", "http.server", "8080", "--bind", "0.0.0.0"
    ]
    assert workspace.preview_status({"preview_id": preview["preview_id"]})["status"] == "running"
    assert workspace.console_output({"preview_id": preview["preview_id"]})["output"] == "server ready"
    assert workspace.stop_preview({"preview_id": preview["preview_id"]})["status"] == "stopped"
    assert runner.stopped == [preview["preview_id"]]


@pytest.mark.parametrize("arguments", [
    {"project": "site", "port": 80},
    {"project": "site", "port": 70000},
    {"project": "site", "port": True},
    {"project": "site", "command": "python -m http.server"},
    {"project": "site", "command": ["bash", "-c", "echo unsafe"]},
])
def test_preview_rejects_invalid_port_shell_and_unapproved_command(tmp_path, arguments):
    workspace, runner = make_workspace(tmp_path)
    workspace.create_project({"project": "site"})
    with pytest.raises(CodingWorkspaceError):
        workspace.start_preview(arguments)
    assert not runner.previews


def test_preview_fails_closed_when_runner_is_unavailable(tmp_path):
    workspace, _ = make_workspace(tmp_path)

    class OfflineRunner(FakeRunner):
        def start_preview(self, payload):
            raise SandboxUnavailable("runner offline; host fallback disabled")

    workspace.runner = OfflineRunner()
    workspace.create_project({"project": "site"})
    with pytest.raises(CodingWorkspaceError, match="host fallback disabled"):
        workspace.start_preview({"project": "site"})


def test_snapshot_export_persistence_and_secret_exclusion(tmp_path):
    workspace, _ = make_workspace(tmp_path)
    workspace.create_project({"project": "durable"})
    workspace.write_file({"project": "durable", "path": "index.html", "content": "<h1>Persist</h1>"})
    workspace.write_file({"project": "durable", "path": ".env", "content": "TOKEN=do-not-export"})
    snapshot = workspace.snapshot({"project": "durable"})
    export = workspace.export_project({"project": "durable"})
    assert Path(snapshot["path"]).is_file()
    assert Path(export["path"]).is_file()
    with zipfile.ZipFile(export["path"]) as archive:
        names = archive.namelist()
        assert "index.html" in names
        assert ".env" not in names
        assert all(".openmanus" not in name for name in names)

    # Reopening the workspace against the same root simulates application restart.
    reopened = CodingWorkspace(tmp_path / "projects", runner=FakeRunner())
    assert reopened.read_file({"project": "durable", "path": "index.html"})["content"] == "<h1>Persist</h1>"
    assert Path(snapshot["path"]).is_file()


def test_project_and_file_paths_fail_closed(tmp_path):
    workspace, _ = make_workspace(tmp_path)
    workspace.create_project({"project": "safe"})
    with pytest.raises(CodingWorkspaceError):
        workspace.create_project({"project": "../outside"})
    with pytest.raises(CodingWorkspaceError):
        workspace.write_file({"project": "safe", "path": "../outside.txt", "content": "no"})

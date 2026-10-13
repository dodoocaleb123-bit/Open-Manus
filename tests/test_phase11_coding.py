import time
from pathlib import Path

import pytest

from coding import CodingWorkspace, CodingWorkspaceError
from api import OpenManusAPI
from execution import ControlledExecutor, SQLiteStateStore
from model_adapters import ModelRole


class FakeSandboxRunner:
    """Protocol double: tests the adapter contract without running host code."""

    def run(self, payload):
        timed_out = payload.get("timeout_seconds") == 1 and "sleep" in payload["command"][-1]
        return {
            "job_id": "test-job",
            "returncode": 124 if timed_out else 0,
            "stdout": "",
            "stderr": "",
            "duration_seconds": 1.0,
            "status": "timed_out" if timed_out else "passed",
        }


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def test_workspace_execution_fails_closed_without_runner(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENMANUS_SANDBOX_URL", raising=False)
    monkeypatch.delenv("OPENMANUS_SANDBOX_TOKEN", raising=False)
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    with pytest.raises(CodingWorkspaceError, match="Host execution fallback is disabled"):
        workspace.run_command({"project": "demo", "command": ["python", "-c", "print('should not run')"]})
    with pytest.raises(CodingWorkspaceError, match="Secure previews are unavailable"):
        workspace.start_preview({"project": "demo", "port": 18768})


def test_workspace_files_commands_snapshots_and_export(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects", runner=FakeSandboxRunner())
    created = workspace.create_project({"project": "demo"})
    assert created["created"] is True
    workspace.write_file({"project": "demo", "path": "tests/test_ok.py", "content": "def test_ok():\n    assert 2 + 2 == 4\n"})
    workspace.write_file({"project": "demo", "path": "README.md", "content": "demo"})
    assert any(item["path"] == "README.md" for item in workspace.file_tree({"project": "demo"}))
    result = workspace.run_tests({"project": "demo", "command": ["pytest", "-q"]})
    assert result["status"] == "passed"
    assert result["returncode"] == 0
    project = tmp_path / "projects" / "demo"
    (project / ".env").write_text("API_KEY=do-not-export", encoding="utf-8")
    (project / "private.pem").write_text("private-key", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (project / "outside-link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    snapshot = workspace.snapshot({"project": "demo"})
    export = workspace.export_project({"project": "demo"})
    assert Path(snapshot["path"]).is_file()
    assert Path(export["path"]).is_file()
    import zipfile
    for archive_path in (snapshot["path"], export["path"]):
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        assert "README.md" in names
        assert not any(name.startswith(".openmanus/") for name in names)
        assert ".env" not in names
        assert "private.pem" not in names
        assert "outside-link.txt" not in names


def test_workspace_rejects_escape_and_unallowlisted_commands(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects", runner=FakeSandboxRunner())
    workspace.create_project({"project": "demo"})
    with pytest.raises(CodingWorkspaceError, match="escapes"):
        workspace.write_file({"project": "demo", "path": "../secret.txt", "content": "no"})
    with pytest.raises(CodingWorkspaceError, match="allowlisted"):
        workspace.run_command({"project": "demo", "command": ["bash", "-c", "echo unsafe"]})
    with pytest.raises(CodingWorkspaceError, match="package-manager"):
        workspace.run_command({"project": "demo", "command": ["npm", "exec", "unsafe"]})


def test_preview_fails_closed_until_isolated_preview_proxy_exists(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects", runner=FakeSandboxRunner())
    workspace.create_project({"project": "demo"})
    with pytest.raises(CodingWorkspaceError, match="Secure previews are unavailable"):
        workspace.start_preview({"project": "demo", "port": 18765})


def test_api_registers_coder_tools_and_keeps_model_roles_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = SQLiteStateStore(tmp_path / "coding.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"coding_create_project", "coding_write_file", "coding_run_tests", "coding_run_build", "coding_snapshot", "coding_export_project", "coding_start_preview"} <= set(executor._tools)
    assert api.catalog()["agents"][1]["model"] == "qwen2.5:3b"
    assert api.catalog()["agents"][2]["model"] == "qwen2.5-coder:7b"
    store.close()


def test_workspace_timeout_result_is_preserved_from_runner(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects", runner=FakeSandboxRunner())
    workspace.create_project({"project": "timeout"})
    result = workspace.run_command({
        "project": "timeout",
        "command": ["python", "-c", "import time; time.sleep(10)"],
        "timeout_seconds": 1,
    })
    assert result["status"] == "timed_out"
    assert result["returncode"] == 124


def test_created_web_app_preview_fails_closed_without_proxy(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects", runner=FakeSandboxRunner())
    workspace.create_project({"project": "todo"})
    workspace.write_file({
        "project": "todo",
        "path": "index.html",
        "content": "<!doctype html><title>Todo</title><h1>My tasks</h1>",
    })
    with pytest.raises(CodingWorkspaceError, match="Secure previews are unavailable"):
        workspace.start_preview({"project": "todo", "port": 18767})



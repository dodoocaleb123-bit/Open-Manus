import time
from pathlib import Path

import pytest

from coding import CodingWorkspace, CodingWorkspaceError
from api import OpenManusAPI
from execution import ControlledExecutor, SQLiteStateStore
from model_adapters import ModelRole, HealthStatus


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def test_workspace_files_commands_snapshots_and_export(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    created = workspace.create_project({"project": "demo"})
    assert created["created"] is True
    workspace.write_file({"project": "demo", "path": "tests/test_ok.py", "content": "def test_ok():\n    assert 2 + 2 == 4\n"})
    workspace.write_file({"project": "demo", "path": "README.md", "content": "demo"})
    assert any(item["path"] == "README.md" for item in workspace.file_tree({"project": "demo"}))
    result = workspace.run_tests({"project": "demo", "command": ["pytest", "-q"]})
    assert result["status"] == "passed"
    assert result["returncode"] == 0
    snapshot = workspace.snapshot({"project": "demo"})
    export = workspace.export_project({"project": "demo"})
    assert Path(snapshot["path"]).is_file()
    assert Path(export["path"]).is_file()


def test_workspace_rejects_escape_and_unallowlisted_commands(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    with pytest.raises(CodingWorkspaceError, match="escapes"):
        workspace.write_file({"project": "demo", "path": "../secret.txt", "content": "no"})
    with pytest.raises(CodingWorkspaceError, match="allowlisted"):
        workspace.run_command({"project": "demo", "command": ["bash", "-c", "echo unsafe"]})
    with pytest.raises(CodingWorkspaceError, match="package-manager"):
        workspace.run_command({"project": "demo", "command": ["npm", "exec", "unsafe"]})


def test_preview_server_lifecycle(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    preview = workspace.start_preview({"project": "demo", "port": 18765})
    try:
        time.sleep(0.15)
        status = workspace.preview_status({"preview_id": preview["preview_id"]})
        assert status["status"] == "running"
    finally:
        stopped = workspace.stop_preview({"preview_id": preview["preview_id"]})
    assert stopped["status"] == "stopped"


def test_api_registers_coder_tools_and_keeps_model_roles_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = SQLiteStateStore(tmp_path / "coding.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"coding_create_project", "coding_write_file", "coding_run_tests", "coding_run_build", "coding_snapshot", "coding_export_project", "coding_start_preview"} <= set(executor._tools)
    assert api.catalog()["agents"][1]["model"] == "qwen2.5:3b"
    assert api.catalog()["agents"][2]["model"] == "qwen2.5-coder:7b"
    store.close()

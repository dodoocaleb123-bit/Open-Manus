import asyncio
import subprocess

import pytest

from api import OpenManusAPI
from coding import CodingWorkspace
from execution import ControlledExecutor, SQLiteStateStore
from model_adapters import CommandType, ControllerCommand, ModelRole, parse_decision
from security import SecureActions


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def run(coro):
    return asyncio.run(coro)


def test_approval_protocol_accepts_all_phase13_actions():
    for action in ("push_to_github", "send_external_message", "publish_or_deploy", "modify_connected_service", "delete_user_data", "use_or_change_sensitive_secrets", "make_irreversible_change"):
        decision = parse_decision('{"assistant_message":"Approval needed","status":"waiting_for_approval","command":{"type":"request_user_approval","arguments":{"action":"' + action + '","summary":"Review this","impact":"External or irreversible effect"}}}')
        assert decision.command.arguments["action"] == action


def test_secure_github_push_is_blocked_until_approval(tmp_path):
    projects = tmp_path / "projects"
    workspace = CodingWorkspace(projects)
    workspace.create_project({"project": "demo"})
    project = projects / "demo"
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    secure = SecureActions(workspace)
    spec = next(item for item in secure.specs() if item.name == "secure_github_push")
    store = SQLiteStateStore(tmp_path / "secure.sqlite3")
    executor = ControlledExecutor(Registry(), store=store, tools=(__import__("execution", fromlist=["ToolSpec"]).ToolSpec(spec.name, spec.handler, spec.approval_action, spec.description),))
    task = executor.create_task("Push the approved project")
    command = ControllerCommand(CommandType.RUN_TOOL, {"tool": "secure_github_push", "arguments": {"project": "demo", "remote": "origin", "branch": "main"}})
    first = run(executor.execute(task.task_id, command))
    assert first.waiting_for == "approval"
    assert executor.store.snapshot(task.task_id).pending_approval["action"] == "push_to_github"
    store.close()


def test_preview_shows_changed_files_and_delete_requires_approval(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    workspace.write_file({"project": "demo", "path": "draft.txt", "content": "delete me"})
    secure = SecureActions(workspace)
    preview = secure.preview_changes({"project": "demo"})
    assert preview["repository"] is False
    spec = next(item for item in secure.specs() if item.name == "secure_delete_files")
    store = SQLiteStateStore(tmp_path / "delete.sqlite3")
    from execution import ToolSpec
    executor = ControlledExecutor(Registry(), store=store, tools=(ToolSpec(spec.name, spec.handler, spec.approval_action, spec.description),))
    task = executor.create_task("Delete the draft")
    command = ControllerCommand(CommandType.RUN_TOOL, {"tool": "secure_delete_files", "arguments": {"project": "demo", "paths": ["draft.txt"]}})
    first = run(executor.execute(task.task_id, command))
    assert first.waiting_for == "approval"
    assert (tmp_path / "projects" / "demo" / "draft.txt").exists()
    store.close()


def test_api_registers_secure_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = SQLiteStateStore(tmp_path / "api.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"secure_preview_changes", "secure_github_push", "secure_send_message", "secure_publish", "secure_modify_service", "secure_delete_files", "secure_use_secret", "secure_irreversible_change"} <= set(executor._tools)
    assert executor._tools["secure_github_push"].approval_action == "push_to_github"
    store.close()

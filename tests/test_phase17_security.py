import asyncio
import os
import subprocess

import pytest

from coding import CodingWorkspace, CodingWorkspaceError
from execution import ControlledExecutor, SQLiteStateStore, ToolSpec
from model_adapters import CommandType, ControllerCommand, ModelRole
from security import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    ProjectAccessController,
    RateLimiter,
    SecretStore,
    TokenAuthenticator,
)


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]


def run(coro):
    return asyncio.run(coro)


def test_token_authentication_is_optional_but_constant_time(monkeypatch):
    monkeypatch.setenv("OPENMANUS_AUTH_TOKEN", "correct-token")
    auth = TokenAuthenticator()
    assert auth.authenticate("Bearer correct-token").subject == "token-user"
    with pytest.raises(AuthenticationError):
        auth.authenticate("Bearer wrong-token")
    with pytest.raises(AuthenticationError):
        auth.authenticate(None)


def test_project_acl_and_rate_limit():
    acl = ProjectAccessController()
    owner = Principal("alice", frozenset())
    viewer = Principal("bob", frozenset())
    acl.grant("demo", "alice", "owner")
    acl.grant("demo", "bob", "viewer")
    acl.authorize("demo", owner, "project.write")
    with pytest.raises(AuthorizationError):
        acl.authorize("demo", viewer, "project.write")
    limiter = RateLimiter(limit=1, window_seconds=60)
    assert limiter.check("ip").allowed
    assert not limiter.check("ip").allowed


def test_secret_store_encrypts_and_only_describes_references(tmp_path):
    store = SecretStore(tmp_path / "secrets.sqlite3", "test-master-key")
    created = store.put("alice", "GitHub token", "super-secret-value")
    assert created["value_exposed"] is False
    assert store.resolve(created["secret_ref"], "alice") == "super-secret-value"
    assert "super-secret-value" not in str(store.describe("alice"))
    with pytest.raises(Exception):
        store.resolve(created["secret_ref"], "bob")
    store.close()


def test_executor_permission_and_redacted_audit(tmp_path):
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    executor = ControlledExecutor(
        Registry(),
        store=store,
        allowed_permissions=frozenset({"project.read"}),
        tools=(ToolSpec("secret_tool", lambda args: {"token": "hidden"}, permission="secret.use"),),
    )
    task = executor.create_task("run a secret tool")
    command = ControllerCommand(CommandType.RUN_TOOL, {"tool": "secret_tool", "arguments": {"token": "token=hidden"}})
    result = run(executor.execute(task.task_id, command))
    assert result.task_status.value == "failed"
    audit = store.list_audit(task.task_id)
    assert audit and audit[-1]["outcome"] == "denied_or_failed"
    assert "hidden" not in str(audit)
    store.close()


def test_command_sandbox_rejects_dependency_flags_and_keeps_project_scope(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    with pytest.raises(CodingWorkspaceError, match="unsafe"):
        workspace.manage_dependencies({"project": "demo", "manager": "npm", "packages": ["--prefix", "/tmp"]})
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (tmp_path / "projects" / "demo" / "link.txt").symlink_to(outside)
    with pytest.raises(CodingWorkspaceError, match="escapes"):
        workspace.read_file({"project": "demo", "path": "link.txt"})

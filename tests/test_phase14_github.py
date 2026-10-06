import asyncio
import json
import subprocess

from api import OpenManusAPI
from coding import CodingWorkspace
from execution import ControlledExecutor, SQLiteStateStore, ToolSpec
from github import GitHubCommandResult, GitHubIntegration
from model_adapters import CommandType, ModelRole, ControllerCommand


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def run(coro):
    return asyncio.run(coro)


def test_github_auth_repository_and_branch_selection_with_fake_runner(tmp_path):
    calls = []

    def runner(command, *, cwd, timeout):
        calls.append((command, cwd, timeout))
        if command[:3] == ["gh", "auth", "status"]:
            return GitHubCommandResult(0, "Logged in to github.com", "")
        if command[:3] == ["gh", "repo", "list"]:
            return GitHubCommandResult(0, '[{"nameWithOwner":"octo/demo","defaultBranchRef":{"name":"main"}}]', "")
        return GitHubCommandResult(0, '{"name":"main","protected":false}\n{"name":"dev","protected":false}\n', "")

    github = GitHubIntegration(CodingWorkspace(tmp_path / "projects"), runner=runner)
    assert github.auth_status({})["authenticated"] is True
    assert github.list_repositories({})["repositories"][0]["nameWithOwner"] == "octo/demo"
    assert [item["name"] for item in github.list_branches({"repo": "octo/demo"})["branches"]] == ["main", "dev"]
    assert len(calls) == 3


def test_github_diff_and_local_commit_are_separate_from_push(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    project = tmp_path / "projects" / "demo"
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    workspace.write_file({"project": "demo", "path": "README.md", "content": "hello"})

    def runner(command, *, cwd, timeout):
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
        return GitHubCommandResult(completed.returncode, completed.stdout, completed.stderr)

    github = GitHubIntegration(workspace, runner=runner)
    diff = github.diff({"project": "demo"})
    assert any("README.md" in item for item in diff["changed_files"])
    commit = github.create_commit({"project": "demo", "files": ["README.md"], "message": "Add README"})
    assert commit["created"] is True
    assert commit["push_required"] is True


def test_github_push_returns_commit_url_or_retryable_failure(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "demo"})
    subprocess.run(["git", "init", "-q"], cwd=tmp_path / "projects" / "demo", check=True)
    calls = []

    def runner(command, *, cwd, timeout):
        calls.append(command)
        if command[:2] == ["git", "push"]:
            return GitHubCommandResult(1, "", "authentication failed")
        if command[:3] == ["git", "branch", "--show-current"]:
            return GitHubCommandResult(0, "main\n", "")
        if command[:2] == ["git", "rev-parse"]:
            return GitHubCommandResult(0, "abc123\n", "")
        return GitHubCommandResult(0, "", "")

    github = GitHubIntegration(workspace, runner=runner)
    failed = github.push({"project": "demo", "repo": "octo/demo", "branch": "main"})
    assert failed["pushed"] is False
    assert failed["retryable"] is True
    assert "authentication failed" in failed["error"]


def test_api_registers_github_tools_and_push_requires_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_PROJECTS_ROOT", str(tmp_path / "projects"))
    store = SQLiteStateStore(tmp_path / "github.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"github_auth_status", "github_list_repositories", "github_list_branches", "github_diff", "github_create_commit", "github_push", "github_retry_push"} <= set(executor._tools)
    assert executor._tools["github_push"].approval_action == "push_to_github"
    task = executor.create_task("Push to GitHub")
    command = ControllerCommand(CommandType.RUN_TOOL, {"tool": "github_push", "arguments": {"project": "demo", "repo": "octo/demo", "branch": "main"}})
    result = run(executor.execute(task.task_id, command))
    assert result.waiting_for == "approval"
    store.close()

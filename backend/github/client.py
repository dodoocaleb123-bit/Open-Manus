"""Controlled GitHub integration exposed as DeepSeek-selected tools."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from coding import CodingWorkspace, CodingWorkspaceError


class GitHubIntegrationError(RuntimeError):
    """Raised for invalid GitHub integration requests."""


@dataclass(frozen=True)
class GitHubCommandResult:
    returncode: int
    stdout: str
    stderr: str


class GitHubIntegration:
    """Use the authenticated gh CLI and local git without bypassing DeepSeek."""

    def __init__(self, workspace: CodingWorkspace, *, runner: Callable[..., GitHubCommandResult] | None = None, gh_executable: str = "gh") -> None:
        self.workspace = workspace
        self.runner = runner or self._run
        self.gh_executable = gh_executable

    def auth_status(self, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self.runner([self.gh_executable, "auth", "status", "--hostname", "github.com"], cwd=None, timeout=30)
        return {
            "authenticated": result.returncode == 0,
            "provider": "github",
            "detail": self._last_text(result),
            "credentials_exposed": False,
            "retryable": result.returncode != 0,
        }

    def list_repositories(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        limit = min(max(int(arguments.get("limit", 50)), 1), 100)
        result = self.runner([self.gh_executable, "repo", "list", "--json", "nameWithOwner,defaultBranchRef,visibility,url,isPrivate", "--limit", str(limit)], cwd=None, timeout=60)
        self._raise_or_result(result, "list repositories")
        try:
            repositories = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GitHubIntegrationError("GitHub returned invalid repository data") from exc
        return {"repositories": repositories, "count": len(repositories), "selected": arguments.get("repo")}

    def list_branches(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        repo = self._repo(arguments)
        result = self.runner([self.gh_executable, "api", f"repos/{repo}/branches", "--paginate", "--jq", ".[] | {name: .name, protected: .protected}"], cwd=None, timeout=60)
        self._raise_or_result(result, f"list branches for {repo}")
        branches = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                branches.append(json.loads(line))
            except json.JSONDecodeError:
                branches.append({"name": line.strip()})
        return {"repo": repo, "branches": branches, "selected": arguments.get("branch")}

    def diff(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        if not (project / ".git").exists():
            raise GitHubIntegrationError("GitHub diff requires a Git repository")
        base = self._safe_ref(arguments.get("base", "HEAD"), "base")
        status = self._git(project, ["status", "--short"])
        diff = self._git(project, ["diff", "--no-ext-diff", base])
        staged = self._git(project, ["diff", "--cached", "--no-ext-diff"])
        return {
            "project": project.name,
            "base": base,
            "changed_files": [line for line in status.stdout.splitlines() if line.strip()],
            "diff": diff.stdout[-100000:],
            "staged_diff": staged.stdout[-100000:],
            "approval_required_before_push": True,
        }

    def create_commit(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        if not (project / ".git").exists():
            raise GitHubIntegrationError("Commit creation requires a Git repository")
        message = str(arguments.get("message", "")).strip()
        if not message or len(message) > 200:
            raise GitHubIntegrationError("commit message must be 1-200 characters")
        files = arguments.get("files", [])
        if not isinstance(files, list) or not files:
            raise GitHubIntegrationError("create_commit requires an explicit files list")
        safe_files = [self._safe_file(project, item) for item in files]
        add = self._git(project, ["add", "--", *safe_files])
        if add.returncode != 0:
            raise GitHubIntegrationError(f"git add failed: {add.stderr[-4000:]}")
        commit = self._git(project, ["commit", "-m", message])
        if commit.returncode != 0:
            return {"created": False, "status": "nothing_to_commit_or_failed", "stdout": commit.stdout, "stderr": commit.stderr, "retryable": False}
        sha = self._git(project, ["rev-parse", "HEAD"]).stdout.strip()
        branch = self._git(project, ["branch", "--show-current"]).stdout.strip()
        return {"created": True, "project": project.name, "commit_sha": sha, "branch": branch, "message": message, "files": safe_files, "push_required": True, "approval_action": "push_to_github"}

    def push(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        repo = self._repo(arguments, optional=True)
        remote = self._safe_ref(arguments.get("remote", "origin"), "remote")
        branch = self._safe_ref(arguments.get("branch", ""), "branch")
        if not branch:
            branch = self._git(project, ["branch", "--show-current"]).stdout.strip()
        if not branch:
            raise GitHubIntegrationError("push requires a branch")
        if not (project / ".git").exists():
            raise GitHubIntegrationError("push requires a Git repository")
        result = self._git(project, ["push", remote, branch], timeout=180)
        if result.returncode != 0:
            return {"pushed": False, "status": "failed", "project": project.name, "remote": remote, "branch": branch, "repo": repo, "error": self._last_text(result), "retryable": True, "recovery": "Review authentication, remote, branch protection, or network state, then retry the same approved push."}
        sha = self._git(project, ["rev-parse", "HEAD"]).stdout.strip()
        commit_url = f"https://github.com/{repo}/commit/{sha}" if repo and sha else None
        return {"pushed": True, "status": "succeeded", "project": project.name, "remote": remote, "branch": branch, "repo": repo, "commit_sha": sha, "commit_url": commit_url, "stdout": result.stdout, "retryable": False, "approved_by_execution_gate": True}

    def specs(self) -> tuple[tuple[str, Callable[[Mapping[str, Any]], Any], str | None, str], ...]:
        return (
            ("github_auth_status", self.auth_status, None, "Check GitHub CLI authentication without exposing credentials"),
            ("github_list_repositories", self.list_repositories, None, "List authenticated GitHub repositories for selection"),
            ("github_list_branches", self.list_branches, None, "List branches for a selected GitHub repository"),
            ("github_diff", self.diff, None, "Display local changed files and diffs before push approval"),
            ("github_create_commit", self.create_commit, None, "Create a local commit from explicitly selected files"),
            ("github_push", self.push, "push_to_github", "Push an approved commit to the selected GitHub branch"),
            ("github_retry_push", self.push, "push_to_github", "Retry a failed approved GitHub push"),
        )

    def _repo(self, arguments: Mapping[str, Any], *, optional: bool = False) -> str | None:
        value = str(arguments.get("repo", "")).strip()
        if not value and optional:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise GitHubIntegrationError("repo must be in OWNER/REPOSITORY format")
        return value

    @staticmethod
    def _safe_ref(value: Any, label: str) -> str:
        ref = str(value or "").strip()
        if not ref:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", ref) or ref.startswith("-") or ".." in ref:
            raise GitHubIntegrationError(f"invalid Git {label}")
        return ref

    @staticmethod
    def _safe_file(project: Path, value: Any) -> str:
        relative = Path(str(value))
        if relative.is_absolute() or not relative.as_posix() or ".." in relative.parts:
            raise GitHubIntegrationError("commit file path escapes project")
        path = (project / relative).resolve()
        if project not in path.parents:
            raise GitHubIntegrationError("commit file path escapes project")
        return relative.as_posix()

    @staticmethod
    def _last_text(result: GitHubCommandResult) -> str:
        return (result.stderr or result.stdout).strip()[-10000:]

    @staticmethod
    def _raise_or_result(result: GitHubCommandResult, operation: str) -> None:
        if result.returncode != 0:
            raise GitHubIntegrationError(f"GitHub could not {operation}: {(result.stderr or result.stdout).strip()[-4000:]}")

    @staticmethod
    def _run(command: Sequence[str], *, cwd: Path | None, timeout: int) -> GitHubCommandResult:
        try:
            completed = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, env={key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "GH_HOST", "GH_CONFIG_DIR", "GIT_TERMINAL_PROMPT"}})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return GitHubCommandResult(124, "", str(exc))
        return GitHubCommandResult(completed.returncode, completed.stdout, completed.stderr)

    def _git(self, project: Path, args: list[str], timeout: int = 60) -> GitHubCommandResult:
        return self.runner(["git", *args], cwd=project, timeout=timeout)

"""Approval-gated actions for external, destructive, and irreversible operations."""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from coding import CodingWorkspace, CodingWorkspaceError


class SecureActionError(RuntimeError):
    """Raised when a secure action is unavailable or unsafe."""


@dataclass(frozen=True)
class SecureActionSpec:
    name: str
    approval_action: str | None
    description: str
    handler: Callable[[Mapping[str, Any]], Any]


class SecureActions:
    """Handlers whose dangerous operations are gated by ToolSpec approval_action."""

    def __init__(self, coding_workspace: CodingWorkspace, *, connectors: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None) -> None:
        self.workspace = coding_workspace
        self.connectors = dict(connectors or {})

    def specs(self) -> tuple[SecureActionSpec, ...]:
        return (
            SecureActionSpec("secure_preview_changes", None, "Show changed files and diff summary before approval", self.preview_changes),
            SecureActionSpec("secure_github_push", "push_to_github", "Push approved project changes to GitHub", self.github_push),
            SecureActionSpec("secure_send_message", "send_external_message", "Send an approved external message", self.send_message),
            SecureActionSpec("secure_publish", "publish_or_deploy", "Publish or deploy an approved project", self.publish),
            SecureActionSpec("secure_modify_service", "modify_connected_service", "Modify an approved connected service", self.modify_service),
            SecureActionSpec("secure_delete_files", "delete_user_data", "Delete approved project files", self.delete_files),
            SecureActionSpec("secure_use_secret", "use_or_change_sensitive_secrets", "Use or change an approved sensitive secret", self.use_secret),
            SecureActionSpec("secure_irreversible_change", "make_irreversible_change", "Perform an approved irreversible change", self.irreversible_change),
        )

    def preview_changes(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        if not (project / ".git").exists():
            return {"project": project.name, "repository": False, "changed_files": [], "diff_stat": "No Git repository initialized."}
        status = self._git(project, ["status", "--short"])
        diff = self._git(project, ["diff", "--stat"])
        return {"project": project.name, "repository": True, "changed_files": [line for line in status["stdout"].splitlines() if line.strip()], "diff_stat": diff["stdout"], "approval_required": True}

    def github_push(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        remote = self._safe_git_name(arguments.get("remote", "origin"), "remote")
        branch = self._safe_git_name(arguments.get("branch", ""), "branch")
        if not (project / ".git").exists():
            raise SecureActionError("GitHub push requires a Git repository")
        if not branch:
            branch_result = self._git(project, ["branch", "--show-current"])
            branch = self._safe_git_name(branch_result["stdout"].strip(), "branch")
        if not branch:
            raise SecureActionError("GitHub push requires a current branch")
        result = self._git(project, ["push", remote, branch], timeout=180)
        return {"action": "github_push", "project": project.name, "remote": remote, "branch": branch, "returncode": result["returncode"], "stdout": result["stdout"], "stderr": result["stderr"], "approved_by_execution_gate": True}

    def send_message(self, arguments: Mapping[str, Any]) -> Any:
        return self._connector("send_external_message", arguments, "No external messaging connector is configured")

    def publish(self, arguments: Mapping[str, Any]) -> Any:
        return self._connector("publish_or_deploy", arguments, "No publishing connector is configured")

    def modify_service(self, arguments: Mapping[str, Any]) -> Any:
        return self._connector("modify_connected_service", arguments, "No connected-service connector is configured")

    def delete_files(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self.workspace._project(arguments.get("project", ""))
        paths = arguments.get("paths", [])
        if not isinstance(paths, list) or not paths:
            raise SecureActionError("secure_delete_files requires a non-empty paths list")
        deleted = []
        for value in paths:
            _, path = self.workspace._file_path({"project": project.name, "path": value})
            if path == project or not path.exists():
                continue
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(str(path.relative_to(project)))
        return {"action": "delete_files", "project": project.name, "deleted": deleted, "approved_by_execution_gate": True}

    def use_secret(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        secret_ref = str(arguments.get("secret_ref", "")).strip()
        if not secret_ref or any(value in secret_ref.lower() for value in ("token", "password", "key")):
            # References are allowed, values never are; do not echo a sensitive value.
            raise SecureActionError("Provide a non-sensitive secret reference; secret values are never accepted in task arguments")
        return {"action": "use_secret", "secret_ref": secret_ref, "value_exposed": False, "approved_by_execution_gate": True}

    def irreversible_change(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        handler = self.connectors.get("make_irreversible_change")
        if handler is None:
            raise SecureActionError("No irreversible-action connector is configured")
        return handler(arguments)

    def _connector(self, name: str, arguments: Mapping[str, Any], message: str) -> Any:
        handler = self.connectors.get(name)
        if handler is None:
            raise SecureActionError(message)
        return handler(arguments)

    @staticmethod
    def _safe_git_name(value: Any, label: str) -> str:
        name = str(value or "").strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9._/-]+", name) or name.startswith("-") or ".." in name:
            raise SecureActionError(f"invalid Git {label}")
        return name

    @staticmethod
    def _git(project: Path, args: list[str], timeout: int = 30) -> dict[str, Any]:
        completed = subprocess.run(["git", *args], cwd=project, capture_output=True, text=True, timeout=timeout, check=False, env={key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "LANG", "LC_ALL", "GIT_TERMINAL_PROMPT"}})
        return {"returncode": completed.returncode, "stdout": completed.stdout[-50000:], "stderr": completed.stderr[-50000:]}

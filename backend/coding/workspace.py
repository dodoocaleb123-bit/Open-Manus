"""Controlled coding workspace tools for Qwen2.5-Coder:7b."""
from __future__ import annotations

import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .runner_client import SandboxRunnerClient, SandboxUnavailable


class CodingWorkspaceError(ValueError):
    """Raised when a coding workspace operation is unsafe or invalid."""


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodingWorkspace:
    """Manage project workspaces without allowing arbitrary host access."""

    ALLOWED_COMMANDS = {"python", "python3", "pytest", "npm", "npx", "node"}
    SAFE_NPM_SUBCOMMANDS = {"test", "run", "install", "ci", "build", "lint", "check"}

    def __init__(
        self,
        root: str | Path = "./workspace/projects",
        *,
        timeout_seconds: int = 120,
        runner: SandboxRunnerClient | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.runner = runner or SandboxRunnerClient()

    def create_project(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self._project(arguments.get("project", arguments.get("name", "")))
        project.mkdir(parents=True, exist_ok=True)
        (project / ".openmanus").mkdir(exist_ok=True)
        return {"project": project.name, "path": str(project), "created": True, "tree": self.file_tree({"project": project.name})}

    def write_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project, path = self._file_path(arguments)
        content = str(arguments.get("content", ""))
        if len(content.encode("utf-8")) > 10 * 1024 * 1024:
            raise CodingWorkspaceError("file content is larger than 10 MB")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"project": project.name, "path": str(path.relative_to(project)), "bytes": path.stat().st_size}

    def read_file(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project, path = self._file_path(arguments)
        if not path.is_file():
            raise CodingWorkspaceError("file does not exist")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise CodingWorkspaceError("file is larger than 10 MB")
        return {"project": project.name, "path": str(path.relative_to(project)), "content": path.read_text(encoding="utf-8", errors="replace")}

    def file_tree(self, arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
        project = self._project(arguments.get("project", ""))
        if not project.is_dir():
            raise CodingWorkspaceError("project does not exist")
        result = []
        for path in sorted(project.rglob("*")):
            if ".openmanus" in path.parts or ".git" in path.parts or "node_modules" in path.parts:
                continue
            if path.is_symlink() and project not in path.resolve().parents:
                raise CodingWorkspaceError("symlink escapes project")
            result.append({"path": str(path.relative_to(project)), "kind": "directory" if path.is_dir() else "file", "size": path.stat().st_size if path.is_file() else None})
        return result

    def manage_dependencies(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        manager = str(arguments.get("manager", "")).strip()
        packages = [str(item) for item in arguments.get("packages", [])]
        if any(not item or item.startswith("-") or any(ch in item for ch in ";&|`$\n\r") for item in packages):
            raise CodingWorkspaceError("dependency names contain unsafe characters")
        if manager != "npm":
            raise CodingWorkspaceError(
                "Only npm operations are currently supported by the isolated runner. "
                "Python dependency installation remains disabled until a controlled dependency-fetch policy is implemented."
            )
        command = ["npm", "install", *packages]
        return self.run_command({**arguments, "command": command})

    def run_tests(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = arguments.get("command") or ["pytest", "-q"]
        return self.run_command({**arguments, "command": command, "operation": "test"})

    def run_build(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = arguments.get("command") or ["npm", "run", "build"]
        return self.run_command({**arguments, "command": command, "operation": "build"})

    def run_command(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = tuple(str(item) for item in arguments.get("command", ()))
        if not command or Path(command[0]).name not in self.ALLOWED_COMMANDS:
            raise CodingWorkspaceError(
                f"command must start with an allowlisted executable: {sorted(self.ALLOWED_COMMANDS)}"
            )
        self._validate_command(command)
        project = self._project(arguments.get("project", ""))
        if not project.is_dir():
            raise CodingWorkspaceError("project does not exist")
        timeout = min(max(int(arguments.get("timeout_seconds", self.timeout_seconds)), 1), 120)
        try:
            result = self.runner.run({
                "project": project.name,
                "command": list(command),
                "timeout_seconds": timeout,
            })
        except SandboxUnavailable as exc:
            raise CodingWorkspaceError(str(exc)) from exc
        required = {"returncode", "stdout", "stderr", "duration_seconds", "status"}
        if not required.issubset(result):
            raise CodingWorkspaceError("sandbox runner returned an incomplete job result")
        return CommandResult(
            command=command,
            cwd=str(project),
            returncode=int(result["returncode"]),
            stdout=str(result["stdout"])[-50000:],
            stderr=str(result["stderr"])[-50000:],
            duration_seconds=float(result["duration_seconds"]),
            status=str(result["status"]),
        ).to_dict()

    def snapshot(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self._project(arguments.get("project", ""))
        if not project.is_dir():
            raise CodingWorkspaceError("project does not exist")
        snapshot_dir = project / ".openmanus" / "snapshots"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        target = snapshot_dir / f"{snapshot_id}.zip"
        self._zip_project(project, target)
        return {"project": project.name, "snapshot_id": snapshot_id, "path": str(target), "kind": "version_snapshot"}

    def export_project(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        project = self._project(arguments.get("project", ""))
        if not project.is_dir():
            raise CodingWorkspaceError("project does not exist")
        export_dir = self.root / "exports"
        export_dir.mkdir(exist_ok=True)
        target = export_dir / f"{project.name}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        self._zip_project(project, target)
        return {"project": project.name, "path": str(target), "download": True, "size": target.stat().st_size}

    def start_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        # Preview serving is deliberately disabled until a runner-managed
        # lifecycle and a local-only proxy are available and integration-tested.
        # Never expose generated servers directly or fall back to host execution.
        raise CodingWorkspaceError(
            "Isolated previews are unavailable until the local-only preview proxy is implemented"
        )

    def stop_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", "")).strip()
        try:
            return self.runner.stop_preview(preview_id)
        except SandboxUnavailable as exc:
            raise CodingWorkspaceError(str(exc)) from exc

    def preview_status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", "")).strip()
        try:
            return self.runner.preview_status(preview_id)
        except SandboxUnavailable as exc:
            raise CodingWorkspaceError(str(exc)) from exc

    def console_output(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", "")).strip()
        try:
            return self.runner.preview_logs(preview_id)
        except SandboxUnavailable as exc:
            raise CodingWorkspaceError(str(exc)) from exc

    def _project(self, value: Any) -> Path:
        name = str(value or "").strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise CodingWorkspaceError("project must be a simple workspace directory name")
        candidate = self.root / name
        if candidate.is_symlink():
            raise CodingWorkspaceError("project directory cannot be a symlink")
        project = candidate.resolve()
        if self.root not in project.parents:
            raise CodingWorkspaceError("project escapes workspace root")
        return project

    def _file_path(self, arguments: Mapping[str, Any]) -> tuple[Path, Path]:
        project = self._project(arguments.get("project", ""))
        relative = Path(str(arguments.get("path", "")))
        if not relative.as_posix() or relative.is_absolute() or ".." in relative.parts:
            raise CodingWorkspaceError("file path escapes the project")
        path = (project / relative).resolve()
        if project not in path.parents and path != project:
            raise CodingWorkspaceError("file path escapes project")
        return project, path

    @classmethod
    def _validate_command(cls, command: tuple[str, ...], *, preview: bool = False) -> None:
        if command[0] in {"npm", "pnpm", "yarn"} and len(command) > 1 and command[1] not in cls.SAFE_NPM_SUBCOMMANDS:
            raise CodingWorkspaceError("package-manager command is not allowlisted")
        if command[0] in {"pip", "pip3"} and len(command) > 1 and command[1] not in cls.SAFE_PIP_SUBCOMMANDS:
            raise CodingWorkspaceError("pip command is not allowlisted")
        if command[0] == "git" and len(command) > 1 and command[1] not in {"status", "diff", "log", "init"}:
            raise CodingWorkspaceError("git command is read-only/initialization allowlisted")
        if preview and command[0] not in {"python", "python3", "node", "npm", "pnpm", "yarn"}:
            raise CodingWorkspaceError("preview command is not allowlisted")

    @staticmethod
    def _zip_project(project: Path, target: Path) -> None:
        """Archive project files without internal state, secrets, or symlinks."""
        secret_names = {".env", ".env.local", ".env.production", "id_rsa", "id_ed25519"}
        secret_suffixes = {".pem", ".key", ".p12", ".pfx"}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in project.rglob("*"):
                relative = path.relative_to(project)
                # Internal snapshots/previews can contain previous archives and
                # logs; excluding the whole directory also prevents recursive
                # snapshot growth and avoids exporting internal state.
                if path == target or ".git" in relative.parts or ".openmanus" in relative.parts:
                    continue
                # Never follow or archive symlinks: their targets may be outside
                # the project and can change between validation and archiving.
                if path.is_symlink():
                    continue
                name = path.name.lower()
                if name in secret_names or name.startswith(".env.") or path.suffix.lower() in secret_suffixes:
                    continue
                if path.is_file():
                    resolved = path.resolve()
                    if project.resolve() not in resolved.parents:
                        continue
                    archive.write(path, relative)

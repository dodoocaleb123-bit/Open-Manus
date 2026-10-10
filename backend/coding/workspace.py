"""Controlled coding workspace tools for Qwen2.5-Coder:7b."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


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


@dataclass
class PreviewProcess:
    preview_id: str
    project_dir: str
    command: tuple[str, ...]
    port: int
    process: subprocess.Popen[str]
    started_at: float
    log_path: str
    log_handle: Any
    viewport: str = "desktop"


class CodingWorkspace:
    """Manage project workspaces without allowing arbitrary host access."""

    ALLOWED_COMMANDS = {
        "python", "python3", "pytest", "pip", "pip3", "uv", "npm", "npx", "node", "yarn", "pnpm", "git"
    }
    SAFE_NPM_SUBCOMMANDS = {"test", "run", "install", "ci", "build", "lint", "check"}
    SAFE_PIP_SUBCOMMANDS = {"install", "list", "show", "freeze"}

    def __init__(self, root: str | Path = "./workspace/projects", *, timeout_seconds: int = 120) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self._previews: dict[str, PreviewProcess] = {}

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
        if manager == "npm":
            command = ["npm", "install", *packages]
        elif manager in {"pip", "pip3"}:
            command = [manager, "install", *packages]
        elif manager == "uv":
            command = ["uv", "add", *packages]
        else:
            raise CodingWorkspaceError("dependency manager must be npm, pip, pip3, or uv")
        return self.run_command({**arguments, "command": command})

    def run_tests(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = arguments.get("command") or ["pytest", "-q"]
        return self.run_command({**arguments, "command": command, "operation": "test"})

    def run_build(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = arguments.get("command") or ["npm", "run", "build"]
        return self.run_command({**arguments, "command": command, "operation": "build"})

    def run_command(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = tuple(str(item) for item in arguments.get("command", ()))
        if not command or command[0] not in self.ALLOWED_COMMANDS:
            raise CodingWorkspaceError(f"command must start with an allowlisted executable: {sorted(self.ALLOWED_COMMANDS)}")
        self._validate_command(command)
        project = self._project(arguments.get("project", ""))
        if not project.is_dir():
            raise CodingWorkspaceError("project does not exist")
        timeout = min(max(int(arguments.get("timeout_seconds", self.timeout_seconds)), 1), 600)
        started = time.monotonic()
        try:
            process_options: dict[str, Any] = {}
            if os.name == "posix":
                # POSIX-only process group and resource limits. Windows does not
                # provide resource/preexec_fn; timeout and command allowlists still apply.
                process_options["start_new_session"] = True
                process_options["preexec_fn"] = self._resource_limits
            elif os.name == "nt":
                process_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            completed = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=timeout, check=False, env=self._safe_environment(), **process_options)
            status = "passed" if completed.returncode == 0 else "failed"
            result = CommandResult(command, str(project), completed.returncode, completed.stdout[-50000:], completed.stderr[-50000:], time.monotonic() - started, status)
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(command, str(project), 124, str(exc.stdout or "")[-50000:], str(exc.stderr or "")[-50000:], time.monotonic() - started, "timed_out")
        return result.to_dict()

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
        project = self._project(arguments.get("project", ""))
        if not project.is_dir():
            raise CodingWorkspaceError("project does not exist")
        command = tuple(str(item) for item in arguments.get("command", ("python3", "-m", "http.server", str(arguments.get("port", 8000)))))
        self._validate_command(command, preview=True)
        port = int(arguments.get("port", 8000))
        if not 1024 <= port <= 65535:
            raise CodingWorkspaceError("preview port must be between 1024 and 65535")
        viewport = str(arguments.get("viewport", "desktop"))
        if viewport not in {"desktop", "mobile"}:
            raise CodingWorkspaceError("viewport must be desktop or mobile")
        log_dir = project / ".openmanus" / "previews"
        log_dir.mkdir(parents=True, exist_ok=True)
        preview_id = f"preview_{uuid.uuid4().hex}"
        log_path = log_dir / f"{preview_id}.log"
        log_handle = log_path.open("a", encoding="utf-8")
        process_options: dict[str, Any] = {}
        if os.name == "posix":
            process_options["start_new_session"] = True
        elif os.name == "nt":
            process_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(command, cwd=project, stdout=log_handle, stderr=subprocess.STDOUT, text=True, env=self._safe_environment(), **process_options)
        self._previews[preview_id] = PreviewProcess(preview_id, str(project), command, port, process, time.time(), str(log_path), log_handle, viewport)
        return {"preview_id": preview_id, "project": project.name, "port": port, "url": f"http://127.0.0.1:{port}", "status": "starting", "viewport": viewport, "viewports": {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}}

    def stop_preview(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", ""))
        preview = self._previews.get(preview_id)
        if preview is None:
            raise CodingWorkspaceError("preview does not exist")
        preview.process.terminate()
        try:
            preview.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            preview.process.kill()
        preview.log_handle.close()
        self._previews.pop(preview_id, None)
        return {"preview_id": preview_id, "status": "stopped"}

    def preview_status(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", ""))
        preview = self._previews.get(preview_id)
        if preview is None:
            raise CodingWorkspaceError("preview does not exist")
        return {"preview_id": preview_id, "project": Path(preview.project_dir).name, "port": preview.port, "status": "running" if preview.process.poll() is None else "stopped", "returncode": preview.process.poll(), "url": f"http://127.0.0.1:{preview.port}", "viewport": preview.viewport, "log_path": preview.log_path}
    def console_output(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        preview_id = str(arguments.get("preview_id", ""))
        preview = self._previews.get(preview_id)
        if preview is None:
            raise CodingWorkspaceError("preview does not exist")
        path = Path(preview.log_path)
        return {"preview_id": preview_id, "project": Path(preview.project_dir).name, "status": "running" if preview.process.poll() is None else "stopped", "output": path.read_text(encoding="utf-8", errors="replace")[-50000:] if path.exists() else "", "log_path": str(path)}

    def _project(self, value: Any) -> Path:
        name = str(value or "").strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise CodingWorkspaceError("project must be a simple workspace directory name")
        project = (self.root / name).resolve()
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
    def _safe_environment() -> dict[str, str]:
        allowed = {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONPATH", "NODE_PATH"}
        return {key: value for key, value in os.environ.items() if key in allowed}

    @staticmethod
    def _resource_limits() -> None:
        """Apply conservative POSIX limits; only passed to subprocesses on POSIX."""
        if os.name != "posix":
            return
        import resource

        cpu = max(1, int(os.getenv("OPENMANUS_COMMAND_CPU_SECONDS", "120")))
        memory = max(128, int(os.getenv("OPENMANUS_COMMAND_MEMORY_MB", "1024"))) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    @staticmethod
    def _zip_project(project: Path, target: Path) -> None:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in project.rglob("*"):
                if path == target or ".git" in path.parts:
                    continue
                if path.is_file():
                    archive.write(path, path.relative_to(project))

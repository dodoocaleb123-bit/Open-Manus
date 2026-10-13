"""Trusted control plane for ephemeral, no-network Docker code jobs.

The workload receives only a bounded tmpfs workspace populated from a sanitized
copy. It never receives a host bind mount, Docker socket, application secrets,
or runner credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

TOKEN = os.environ.get("OPENMANUS_SANDBOX_TOKEN", "")
PROJECTS_ROOT = Path(os.environ.get("OPENMANUS_SANDBOX_PROJECTS_ROOT", "/workspace/projects")).resolve()
IMAGE = os.environ.get("OPENMANUS_SANDBOX_IMAGE", "openmanus-code-runner:latest")
MAX_BODY = 64 * 1024
MAX_OUTPUT = 50_000
MAX_TIMEOUT = 120
MAX_PROJECT_FILES = 5000
MAX_PROJECT_BYTES = 100 * 1024 * 1024
MAX_WORKSPACE_BYTES = 256 * 1024 * 1024
MAX_CONCURRENT_JOBS = 4
JOB_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
EXECUTABLES = {"python", "python3", "pytest", "node", "npm", "npx"}
NPM_SUBCOMMANDS = {"test", "run", "ci", "build", "lint", "check", "install"}
PYTHON_FLAGS = {"-m", "-c", "-B", "-I", "-u", "-q", "-v"}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "credentials.json", "secrets.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


class RequestError(ValueError):
    """Invalid job request."""


def _sensitive(relative: Path) -> bool:
    for part in relative.parts:
        lower = part.lower()
        if lower in {".git", ".openmanus"} or lower in SENSITIVE_NAMES:
            return True
        if lower.startswith(".env.") or Path(lower).suffix in SENSITIVE_SUFFIXES:
            return True
    return False


def validate_job(value: Any) -> tuple[str, list[str], int]:
    if not isinstance(value, dict) or set(value) - {"project", "command", "timeout_seconds"}:
        raise RequestError("job must contain only project, command, and timeout_seconds")
    project = value.get("project")
    if not isinstance(project, str) or not PROJECT_RE.fullmatch(project) or project in {".", ".."}:
        raise RequestError("invalid project identifier")
    command = value.get("command")
    if not isinstance(command, list) or not command or len(command) > 64:
        raise RequestError("command must be a non-empty argv array of at most 64 items")
    if any(not isinstance(part, str) or not part or len(part) > 4096 or "\x00" in part for part in command):
        raise RequestError("invalid command argument")
    executable = Path(command[0]).name.lower()
    if executable not in EXECUTABLES:
        raise RequestError("executable is not allowed")
    if executable in {"npm", "npx"} and (len(command) < 2 or command[1] not in NPM_SUBCOMMANDS):
        raise RequestError("package-manager operation is not allowed")
    if executable == "python" and len(command) > 1 and command[1].startswith("-") and command[1] not in PYTHON_FLAGS:
        raise RequestError("Python flag is not allowed")
    timeout = value.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
        raise RequestError("timeout_seconds must be between 1 and " + str(MAX_TIMEOUT))
    return project, command, timeout


def _copy_workspace(source: Path, destination: Path) -> dict[str, str]:
    """Copy only bounded regular project files; never follow symlinks or copy secrets."""
    destination.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, str] = {}
    total_bytes = 0
    file_count = 0
    for current, directories, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        directories[:] = [
            name for name in directories
            if not _sensitive(relative_dir / name) and not (current_path / name).is_symlink()
        ]
        out_dir = destination / relative_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src = current_path / name
            relative = relative_dir / name
            if _sensitive(relative) or src.is_symlink():
                continue
            try:
                info = src.stat(follow_symlinks=False)
            except OSError:
                continue
            if not src.is_file() or info.st_size > MAX_PROJECT_BYTES:
                continue
            file_count += 1
            total_bytes += info.st_size
            if file_count > MAX_PROJECT_FILES or total_bytes > MAX_PROJECT_BYTES:
                raise RequestError("project exceeds the runner's file-count or source-size limit")
            target = out_dir / name
            shutil.copyfile(src, target, follow_symlinks=False)
            target.chmod(0o666)
            manifest[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    for current, directories, _ in os.walk(destination, topdown=False):
        Path(current).chmod(0o777 if Path(current) == destination else 0o777)
    return manifest


def _safe_sync_back(source: Path, destination: Path, baseline: dict[str, str]) -> None:
    """Copy changed regular files back without following sandbox-created symlinks."""
    count = 0
    total_bytes = 0
    for current, directories, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        directories[:] = [
            name for name in directories
            if not (current_path / name).is_symlink()
            and not _sensitive(relative_dir / name)
        ]
        for name in files:
            src = current_path / name
            relative = relative_dir / name
            if src.is_symlink() or _sensitive(relative):
                continue
            try:
                info = src.stat(follow_symlinks=False)
            except OSError:
                continue
            if not src.is_file():
                continue
            count += 1
            total_bytes += info.st_size
            if count > MAX_PROJECT_FILES or total_bytes > MAX_WORKSPACE_BYTES:
                raise RequestError("job output exceeds the runner's file-count or output-size limit")
            digest = hashlib.sha256(src.read_bytes()).hexdigest()
            if baseline.get(relative.as_posix()) == digest:
                continue
            target = destination / relative
            if not target.resolve(strict=False).is_relative_to(destination.resolve()):
                continue
            # Refuse symlinked parents in the host project. os.replace replaces
            # a symlink leaf rather than following it.
            parent = target.parent
            chain = []
            while parent != destination and destination in parent.parents:
                chain.append(parent)
                parent = parent.parent
            if any(part.is_symlink() for part in chain) or target.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if any(part.is_symlink() for part in chain):
                continue
            temporary = target.with_name(target.name + ".openmanus-" + uuid.uuid4().hex + ".tmp")
            shutil.copyfile(src, temporary, follow_symlinks=False)
            temporary.chmod(0o644)
            os.replace(temporary, target)


def _bounded_stream(stream: Any, target: bytearray) -> None:
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return
        remaining = MAX_OUTPUT - len(target)
        if remaining > 0:
            target.extend(chunk[:remaining])


def execute_job(value: Any) -> dict[str, Any]:
    project_name, command, timeout = validate_job(value)
    if not TOKEN or not PROJECTS_ROOT.is_dir():
        raise RuntimeError("sandbox runner is not configured")
    project = PROJECTS_ROOT / project_name
    if project.is_symlink() or not project.is_dir() or project.resolve().parent != PROJECTS_ROOT:
        raise RequestError("project does not exist or is not a direct workspace child")

    job_id = "om-" + uuid.uuid4().hex[:24]
    # Keep the staging copy on the Docker host filesystem, but never mount that
    # host directory into the untrusted container. Only the container's bounded
    # tmpfs workspace is visible to the generated code.
    stage_root = Path(tempfile.mkdtemp(prefix=".openmanus-sandbox-", dir=str(PROJECTS_ROOT.parent)))
    stage_root.chmod(0o755)
    staged = stage_root / "workspace"
    container_created = False
    baseline: dict[str, str] = {}
    started = time.monotonic()
    result_status = "failed"
    returncode = 125
    stdout_data = bytearray()
    stderr_data = bytearray()
    try:
        baseline = _copy_workspace(project, staged)
        docker_create = [
            "docker", "create", "--name", job_id,
            "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--cap-add", "CHOWN",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "128", "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1.0", "--ulimit", "nofile=256:256",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs", "/workspace:rw,nosuid,nodev,size=256m,mode=1777",
            "--env", "HOME=/tmp",
            "--env", "PATH=/usr/local/bin:/usr/bin:/bin",
            IMAGE, *command,
        ]
        subprocess.run(docker_create, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=30, check=True,
                       env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        container_created = True
        subprocess.run(["docker", "cp", str(staged) + os.sep + ".", job_id + ":/workspace/"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=30, check=True)
        attached = subprocess.Popen(
            ["docker", "start", "--attach", job_id], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        assert attached.stdout is not None and attached.stderr is not None
        out_thread = threading.Thread(target=_bounded_stream, args=(attached.stdout, stdout_data), daemon=True)
        err_thread = threading.Thread(target=_bounded_stream, args=(attached.stderr, stderr_data), daemon=True)
        out_thread.start()
        err_thread.start()
        try:
            returncode = attached.wait(timeout=timeout)
            result_status = "passed" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", "--signal", "KILL", job_id],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, check=False)
            try:
                attached.wait(timeout=10)
            except subprocess.TimeoutExpired:
                attached.kill()
                attached.wait(timeout=5)
            returncode = 124
            result_status = "timed_out"
        out_thread.join(timeout=3)
        err_thread.join(timeout=3)
        # The container has stopped before output is copied back. It cannot
        # continue modifying the staged files during synchronization.
        subprocess.run(["docker", "cp", job_id + ":/workspace/.", str(staged)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=30, check=False)
        _safe_sync_back(staged, project, baseline)
    finally:
        if container_created:
            subprocess.run(["docker", "rm", "--force", job_id], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        shutil.rmtree(stage_root, ignore_errors=True)

    return {
        "job_id": job_id, "returncode": returncode,
        "stdout": stdout_data.decode("utf-8", "replace"),
        "stderr": stderr_data.decode("utf-8", "replace"),
        "duration_seconds": round(time.monotonic() - started, 3),
        "status": result_status,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenManusSandbox/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def _authorized(self) -> bool:
        return bool(TOKEN) and hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + TOKEN)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        ready = bool(TOKEN and PROJECTS_ROOT.is_dir())
        self._send(200 if ready else 503, {"status": "ok" if ready else "not_configured"})

    def do_POST(self) -> None:
        if self.path != "/v1/jobs":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 1 <= int(length) <= MAX_BODY:
            self._send(413, {"error": "invalid request size"})
            return
        if not JOB_SLOTS.acquire(blocking=False):
            self._send(429, {"error": "runner concurrency limit reached"})
            return
        try:
            payload = json.loads(self.rfile.read(int(length)).decode("utf-8"))
            self._send(200, execute_job(payload))
        except (UnicodeDecodeError, json.JSONDecodeError, RequestError) as exc:
            self._send(400, {"error": str(exc)})
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._send(503, {"error": "sandbox execution unavailable", "detail": str(exc)[:500]})
        finally:
            JOB_SLOTS.release()


def main() -> None:
    if not TOKEN:
        raise SystemExit("OPENMANUS_SANDBOX_TOKEN is required")
    if not PROJECTS_ROOT.is_dir():
        raise SystemExit("OPENMANUS_SANDBOX_PROJECTS_ROOT must be an existing directory")
    host = os.environ.get("OPENMANUS_SANDBOX_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENMANUS_SANDBOX_PORT", "8765"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

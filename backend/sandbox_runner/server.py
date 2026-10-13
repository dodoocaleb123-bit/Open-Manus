"""Minimal trusted control plane for ephemeral Docker sandbox jobs.

Run this service separately from the Open-Manus API. It requires access to the
Docker daemon and must be reachable only on a private interface/network. Never
mount the Docker socket into generated-code containers or expose this service
to untrusted networks.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

TOKEN = os.environ.get("OPENMANUS_SANDBOX_TOKEN", "")
PROJECTS_ROOT = Path(os.environ.get("OPENMANUS_SANDBOX_PROJECTS_ROOT", "/workspace/projects")).resolve()
IMAGE = os.environ.get("OPENMANUS_SANDBOX_IMAGE", "openmanus-code-runner:latest")
MAX_BODY = 64 * 1024
MAX_OUTPUT = 50_000
MAX_TIMEOUT = 120
MAX_CONCURRENT_JOBS = 4
JOB_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
EXECUTABLES = {"python": "python", "python3": "python", "pytest": "pytest", "node": "node", "npm": "npm", "npx": "npx"}
NPM_SUBCOMMANDS = {"test", "run", "ci", "build", "lint", "check"}
PYTHON_FLAGS = {"-m", "-c", "-B", "-I", "-u", "-q", "-v"}


class RequestError(ValueError):
    """Invalid job request."""


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


def execute_job(value: Any) -> dict[str, Any]:
    project_name, command, timeout = validate_job(value)
    if not TOKEN or not PROJECTS_ROOT.is_dir():
        raise RuntimeError("sandbox runner is not configured")
    project = PROJECTS_ROOT / project_name
    if project.is_symlink() or not project.is_dir() or project.resolve().parent != PROJECTS_ROOT:
        raise RequestError("project does not exist or is not a direct workspace child")
    job_id = "om-" + uuid.uuid4().hex[:24]
    docker_command = [
        "docker", "run", "--rm", "--name", job_id,
        "--network", "none", "--read-only", "--user", "65534:65534",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", "128", "--memory", "512m", "--memory-swap", "512m",
        "--cpus", "1.0", "--ulimit", "nofile=256:256",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--mount", "type=bind,src=" + str(project) + ",dst=/workspace,rw",
        "--workdir", "/workspace", "--env", "HOME=/tmp",
        "--env", "PATH=/usr/local/bin:/usr/bin:/bin", IMAGE, *command,
    ]
    started = time.monotonic()
    captured: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

    def drain(stream: Any, key: str) -> None:
        # Always drain the pipe to avoid blocking the child, but retain at most
        # MAX_OUTPUT bytes per stream so a hostile command cannot exhaust RAM.
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            remaining = MAX_OUTPUT - len(captured[key])
            if remaining > 0:
                captured[key].extend(chunk[:remaining])

    process = subprocess.Popen(
        docker_command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    assert process.stdout is not None and process.stderr is not None
    out_thread = threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True)
    err_thread = threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True)
    out_thread.start()
    err_thread.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        subprocess.run(
            ["docker", "rm", "--force", job_id], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False,
        )
        returncode = 124
    out_thread.join(timeout=2)
    err_thread.join(timeout=2)
    stdout = captured["stdout"].decode("utf-8", "replace")
    stderr = captured["stderr"].decode("utf-8", "replace")
    return {
        "job_id": job_id, "returncode": returncode,
        "stdout": stdout, "stderr": stderr,
        "duration_seconds": round(time.monotonic() - started, 3),
        "status": "timed_out" if returncode == 124 else ("passed" if returncode == 0 else "failed"),
    }

class Handler(BaseHTTPRequestHandler):
    server_version = "OpenManusSandbox/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

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

"""Trusted control plane for ephemeral, no-network Docker code jobs.

The workload receives only a bounded tmpfs workspace populated from a sanitized
copy. It never receives a host bind mount, Docker socket, application secrets,
or runner credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import tarfile
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
MAX_ACTIVE_PREVIEWS = 4
PREVIEWS: dict[str, dict[str, Any]] = {}
PREVIEW_LOCK = threading.Lock()
PREVIEW_STARTING = 0
PROJECT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
EXECUTABLES = {"python", "python3", "pytest", "node", "npm", "npx"}
NPM_SUBCOMMANDS = {"test", "run", "ci", "build", "lint", "check", "install"}
PYTHON_FLAGS = {"-m", "-c", "-B", "-I", "-u", "-q", "-v"}
SENSITIVE_NAMES = {
    ".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials",
    ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_ed25519", "credentials", "credentials.json",
    "secrets.json", "auth.json", "service-account.json",
}
SENSITIVE_DIRECTORIES = {".ssh", ".aws", ".azure", ".gnupg"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}


class RequestError(ValueError):
    """Invalid job request."""


def _sensitive(relative: Path) -> bool:
    for part in relative.parts:
        lower = part.lower()
        if lower in {".git", ".openmanus"} or lower in SENSITIVE_NAMES or lower in SENSITIVE_DIRECTORIES:
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


def _docker_env() -> dict[str, str]:
    allowed = {
        "PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
        "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}



def _archive_staged(source: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for current, directories, files in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            directories[:] = [name for name in directories if not (current_path / name).is_symlink()]
            for name in directories + files:
                path = current_path / name
                if path.is_symlink():
                    continue
                archive.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    return buffer.getvalue()


def _extract_workspace_archive(data: bytes, destination: Path) -> None:
    count = 0
    total_bytes = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            name = member.name
            posix = PurePosixPath(name)
            if not name or "\\" in name or ":" in name or "\x00" in name:
                continue
            parts = tuple(part for part in posix.parts if part not in {"", "."})
            if posix.is_absolute() or not parts or any(part == ".." for part in parts):
                continue
            relative = Path(*parts)
            if _sensitive(relative):
                continue
            target = destination / relative
            if not target.resolve(strict=False).is_relative_to(destination.resolve()):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                # Never materialize symlinks, hard links, devices, or FIFOs.
                continue
            count += 1
            total_bytes += member.size
            if count > MAX_PROJECT_FILES or total_bytes > MAX_WORKSPACE_BYTES or member.size > MAX_WORKSPACE_BYTES:
                raise RequestError("job output archive exceeds the runner's file-count or size limit")
            parent = target.parent
            chain = []
            while parent != destination and destination in parent.parents:
                chain.append(parent)
                parent = parent.parent
            if any(item.is_symlink() for item in chain) or target.is_symlink() or target.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                continue
            with stream, target.open("wb") as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
            target.chmod(0o666)


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
            "docker", "create", "--name", job_id, "--label", "openmanus.sandbox=true",
            "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--cap-add", "KILL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", "128", "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1.0", "--ulimit", "nofile=256:256",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs", "/workspace:rw,nosuid,nodev,size=256m,mode=1777",
            "--env", "HOME=/tmp",
            "--env", "PATH=/usr/local/bin:/usr/bin:/bin",
            "--entrypoint", "/bin/sleep", IMAGE, "100000000",
        ]
        subprocess.run(docker_create, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=30, check=True, env=_docker_env())
        container_created = True
        subprocess.run(["docker", "start", job_id], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=30, check=True, env=_docker_env())
        staged_archive = _archive_staged(staged)
        seeded = subprocess.run(
            ["docker", "exec", "-i", job_id, "tar", "-x", "--no-same-owner", "-f", "-", "-C", "/workspace"],
            input=staged_archive, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, check=False, env=_docker_env(),
        )
        if seeded.returncode:
            detail = seeded.stderr.decode("utf-8", "replace")[-1000:]
            raise RuntimeError("failed to seed sandbox workspace: " + detail)
        attached = subprocess.Popen(
            ["docker", "exec", "--user", "65534:65534", "--workdir", "/workspace",
             job_id, *command],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_docker_env(),
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
                           stderr=subprocess.DEVNULL, timeout=10, check=False, env=_docker_env())
            try:
                attached.wait(timeout=10)
            except subprocess.TimeoutExpired:
                attached.kill()
                attached.wait(timeout=5)
            returncode = 124
            result_status = "timed_out"
        out_thread.join(timeout=3)
        err_thread.join(timeout=3)
        if result_status != "timed_out":
            # The command may have daemonized children. Kill every remaining
            # process running as the unprivileged job UID before copying its
            # tmpfs output. The container's inert PID 1 runs as root with only
            # CAP_KILL; generated code has UID 65534 and no capabilities.
            cleanup_code = (
                "import os,signal,time\n"
                "deadline=time.monotonic()+2\n"
                "while time.monotonic()<deadline:\n"
                " alive=False\n"
                " for name in os.listdir('/proc'):\n"
                "  if not name.isdigit(): continue\n"
                "  try:\n"
                "   status=open('/proc/'+name+'/status').read()\n"
                "   uid=next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1]\n"
                "   state=next(line for line in status.splitlines() if line.startswith('State:')).split()[1]\n"
                "   if uid=='65534' and state!='Z': os.kill(int(name),signal.SIGKILL); alive=True\n"
                "  except (OSError,IndexError,ProcessLookupError): pass\n"
                " if not alive: break\n"
                " time.sleep(0.05)"
            )
            subprocess.run(["docker", "exec", "--user", "0:0", job_id, "python", "-c", cleanup_code],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, timeout=10, check=True, env=_docker_env())
            exported = subprocess.run(
                ["docker", "exec", job_id, "tar", "-c", "-f", "-", "-C", "/workspace", "."],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=30, check=True, env=_docker_env(),
            )
            if len(exported.stdout) > MAX_WORKSPACE_BYTES + MAX_PROJECT_FILES * 1024:
                raise RequestError("job output archive exceeds the runner's size limit")
            _extract_workspace_archive(exported.stdout, staged)
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



PREVIEW_RUNNER = r"""import json, os, signal, subprocess
command = json.load(open('/tmp/openmanus-preview-command.json', encoding='utf-8'))
log = open('/tmp/openmanus-preview.log', 'ab', buffering=0)
process = subprocess.Popen(
    command, cwd='/workspace', stdout=log, stderr=subprocess.STDOUT,
    env={'HOME': '/tmp', 'PATH': '/usr/local/bin:/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
    start_new_session=True,
)
open('/tmp/openmanus-preview.pid', 'w', encoding='ascii').write(str(process.pid))
code = process.wait()
open('/tmp/openmanus-preview.exit', 'w', encoding='ascii').write(str(code))
try:
    os.killpg(process.pid, signal.SIGKILL)
except ProcessLookupError:
    pass
"""

PREVIEW_PROXY = r"""import select, socket, socketserver, sys, threading
target, target_port = sys.argv[1], int(sys.argv[2])
slots = threading.BoundedSemaphore(32)

class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        if not slots.acquire(False):
            return
        upstream = None
        try:
            upstream = socket.create_connection((target, target_port), timeout=5)
            self.request.settimeout(60)
            upstream.settimeout(60)
            peers = [self.request, upstream]
            while True:
                readable, _, _ = select.select(peers, [], [], 60)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.request else self.request).sendall(data)
        except OSError:
            pass
        finally:
            if upstream is not None:
                upstream.close()
            slots.release()

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32

Server(('0.0.0.0', 8080), Handler).serve_forever()
"""


def _preview_process_state(container_name: str) -> dict[str, Any]:
    code = (
        "import json,os,sys\n"
        "base='/tmp/openmanus-preview.'\n"
        "exit_path=base+'exit'\n"
        "pid_path=base+'pid'\n"
        "if os.path.exists(exit_path):\n"
        " print(json.dumps({'status':'stopped','returncode':int(open(exit_path).read())}))\n"
        "elif not os.path.exists(pid_path):\n"
        " print(json.dumps({'status':'starting'}))\n"
        "else:\n"
        " p=int(open(pid_path).read())\n"
        " try:\n"
        "  state=open('/proc/'+str(p)+'/stat').read().split()[2]\n"
        "  print(json.dumps({'status':'stopped' if state=='Z' else 'running'}))\n"
        " except OSError:\n"
        "  print(json.dumps({'status':'stopped'}))"
    )
    result = subprocess.run(
        ["docker", "exec", container_name, "python", "-c", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=5, check=False, env=_docker_env(),
    )
    if result.returncode:
        return {"status": "stopped"}
    try:
        parsed = json.loads(result.stdout.strip())
        return parsed if isinstance(parsed, dict) else {"status": "stopped"}
    except json.JSONDecodeError:
        return {"status": "stopped"}


def _preview_logs(container_name: str) -> str:
    code = (
        "from pathlib import Path; p=Path('/tmp/openmanus-preview.log'); "
        "print(p.read_text(errors='replace')[-50000:] if p.exists() else '')"
    )
    result = subprocess.run(
        ["docker", "exec", container_name, "python", "-c", code],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=5, check=False, env=_docker_env(),
    )
    return result.stdout[-MAX_OUTPUT:] if result.returncode == 0 else result.stderr[-MAX_OUTPUT:]


def start_preview(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"project", "command", "port"}:
        raise RequestError("preview must contain only project, command, and port")
    port = value.get("port", 8000)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise RequestError("preview port must be an integer between 1024 and 65535")
    project_name, command, _ = validate_job({
        "project": value.get("project"),
        "command": value.get("command"),
        "timeout_seconds": 120,
    })
    if not TOKEN or not PROJECTS_ROOT.is_dir():
        raise RuntimeError("sandbox runner is not configured")
    project = PROJECTS_ROOT / project_name
    if project.is_symlink() or not project.is_dir() or project.resolve().parent != PROJECTS_ROOT:
        raise RequestError("project does not exist or is not a direct workspace child")

    stage_root = Path(tempfile.mkdtemp(prefix=".openmanus-sandbox-", dir=str(PROJECTS_ROOT.parent)))
    stage_root.chmod(0o755)
    global PREVIEW_STARTING
    with PREVIEW_LOCK:
        if len(PREVIEWS) + PREVIEW_STARTING >= MAX_ACTIVE_PREVIEWS:
            shutil.rmtree(stage_root, ignore_errors=True)
            raise RequestError("active preview limit reached")
        PREVIEW_STARTING += 1
    preview_id = uuid.uuid4().hex[:24]
    sandbox_id = "om-prev-" + preview_id
    proxy_id = "om-proxy-" + preview_id
    network_id = "om-net-" + preview_id
    staged = stage_root / "workspace"
    network_created = False
    sandbox_created = False
    proxy_created = False
    try:
        _copy_workspace(project, staged)
        subprocess.run(
            ["docker", "network", "create", "--internal", "--label", "openmanus.sandbox=true", network_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=20, check=True, env=_docker_env(),
        )
        network_created = True
        create_sandbox = [
            "docker", "create", "--name", sandbox_id, "--label", "openmanus.sandbox=true",
            "--network", network_id, "--read-only", "--cap-drop", "ALL", "--cap-add", "KILL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "128",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1.0",
            "--ulimit", "nofile=256:256",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--tmpfs", "/workspace:rw,nosuid,nodev,size=256m,mode=1777",
            "--env", "HOME=/tmp", "--env", "PATH=/usr/local/bin:/usr/bin:/bin",
            "--entrypoint", "/bin/sleep", IMAGE, "100000000",
        ]
        subprocess.run(create_sandbox, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True, timeout=30, check=True, env=_docker_env())
        sandbox_created = True
        subprocess.run(["docker", "start", sandbox_id], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=30, check=True, env=_docker_env())
        seeded = subprocess.run(
            ["docker", "exec", "-i", sandbox_id, "tar", "-x", "--no-same-owner", "-f", "-", "-C", "/workspace"],
            input=_archive_staged(staged), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=30, check=False, env=_docker_env(),
        )
        if seeded.returncode:
            raise RuntimeError("failed to seed preview workspace: " + seeded.stderr.decode("utf-8", "replace")[-1000:])

        assets = stage_root / "assets"
        assets.mkdir()
        (assets / "openmanus-preview-runner.py").write_text(PREVIEW_RUNNER, encoding="utf-8")
        (assets / "openmanus-preview-command.json").write_text(json.dumps(command), encoding="utf-8")
        seeded_assets = subprocess.run(
            ["docker", "exec", "-i", sandbox_id, "tar", "-x", "--no-same-owner", "-f", "-", "-C", "/tmp"],
            input=_archive_staged(assets), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False, env=_docker_env(),
        )
        if seeded_assets.returncode:
            raise RuntimeError("failed to seed preview controller: " + seeded_assets.stderr.decode("utf-8", "replace")[-1000:])
        subprocess.run(
            ["docker", "exec", "--detach", "--user", "65534:65534", "--workdir", "/workspace",
             sandbox_id, "python", "/tmp/openmanus-preview-runner.py"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=10, check=True, env=_docker_env(),
        )

        deadline = time.monotonic() + 5
        state: dict[str, Any] = {"status": "starting"}
        while time.monotonic() < deadline:
            state = _preview_process_state(sandbox_id)
            if state.get("status") == "running":
                break
            if state.get("status") == "stopped":
                raise RequestError("preview process exited during startup: " + _preview_logs(sandbox_id))
            time.sleep(0.1)
        if state.get("status") != "running":
            raise RequestError("preview process did not become ready: " + _preview_logs(sandbox_id))

        subprocess.run(
            ["docker", "create", "--name", proxy_id, "--label", "openmanus.sandbox=true",
             "--network", network_id, "--publish", "127.0.0.1::8080/tcp",
             "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
             "--pids-limit", "32", "--memory", "128m", "--memory-swap", "128m",
             "--cpus", "0.25", "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m",
             "--entrypoint", "python", IMAGE, "-c", PREVIEW_PROXY, sandbox_id, str(port)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=30, check=True, env=_docker_env(),
        )
        proxy_created = True
        subprocess.run(["docker", "start", proxy_id], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       timeout=20, check=True, env=_docker_env())
        mapped = subprocess.run(
            ["docker", "port", proxy_id, "8080/tcp"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=10, check=True, env=_docker_env(),
        ).stdout.strip().splitlines()
        if not mapped or ":" not in mapped[0]:
            raise RuntimeError("preview proxy did not receive a loopback port")
        host_port = int(mapped[0].rsplit(":", 1)[1])
        host = os.environ.get("OPENMANUS_SANDBOX_PREVIEW_HOST", "127.0.0.1")
        screenshot_host = os.environ.get("OPENMANUS_SANDBOX_PREVIEW_INTERNAL_HOST", host)
        record = {
            "preview_id": preview_id, "project": project_name, "status": "running",
            "port": host_port, "container_port": port,
            "url": "http://" + host + ":" + str(host_port),
            "screenshot_url": "http://" + screenshot_host + ":" + str(host_port),
            "sandbox_id": sandbox_id, "proxy_id": proxy_id, "network_id": network_id,
        }
        with PREVIEW_LOCK:
            PREVIEWS[preview_id] = record
        return {key: record[key] for key in ("preview_id", "project", "status", "port", "container_port", "url", "screenshot_url")}
    except Exception:
        if proxy_created:
            subprocess.run(["docker", "rm", "--force", proxy_id], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False, env=_docker_env())
        if sandbox_created:
            subprocess.run(["docker", "rm", "--force", sandbox_id], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False, env=_docker_env())
        if network_created:
            subprocess.run(["docker", "network", "rm", network_id], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False, env=_docker_env())
        raise
    finally:
        with PREVIEW_LOCK:
            PREVIEW_STARTING -= 1
        shutil.rmtree(stage_root, ignore_errors=True)


def preview_status(preview_id: str) -> dict[str, Any]:
    with PREVIEW_LOCK:
        record = PREVIEWS.get(preview_id)
    if record is None:
        raise RequestError("preview not found")
    state = _preview_process_state(record["sandbox_id"])
    return {
        "preview_id": preview_id, "project": record["project"],
        "status": state.get("status", "stopped"),
        "port": record["port"], "url": record["url"],
        "screenshot_url": record["screenshot_url"],
        "returncode": state.get("returncode"),
    }


def preview_logs(preview_id: str) -> dict[str, Any]:
    with PREVIEW_LOCK:
        record = PREVIEWS.get(preview_id)
    if record is None:
        raise RequestError("preview not found")
    return {
        "preview_id": preview_id, "project": record["project"],
        "status": _preview_process_state(record["sandbox_id"]).get("status", "stopped"),
        "output": _preview_logs(record["sandbox_id"]),
    }


def stop_preview(preview_id: str) -> dict[str, Any]:
    with PREVIEW_LOCK:
        record = PREVIEWS.pop(preview_id, None)
    if record is None:
        return {"preview_id": preview_id, "status": "stopped"}
    errors = []
    for container in (record["proxy_id"], record["sandbox_id"]):
        result = subprocess.run(
            ["docker", "rm", "--force", container], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=15, check=False, env=_docker_env(),
        )
        if result.returncode and "No such container" not in result.stderr:
            errors.append(result.stderr[-500:])
    result = subprocess.run(
        ["docker", "network", "rm", record["network_id"]], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        timeout=15, check=False, env=_docker_env(),
    )
    if result.returncode and "not found" not in result.stderr.lower():
        errors.append(result.stderr[-500:])
    if errors:
        raise RuntimeError("preview cleanup incomplete: " + "; ".join(errors))
    return {"preview_id": preview_id, "status": "stopped"}


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
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]
        if path == "/health":
            ready = bool(TOKEN and PROJECTS_ROOT.is_dir())
            self._send(200 if ready else 503, {"status": "ok" if ready else "not_configured"})
            return
        match = re.fullmatch(r"/v1/previews/([A-Za-z0-9-]{1,64})(/logs)?", path)
        if not match:
            self._send(404, {"error": "not found"})
            return
        try:
            result = preview_logs(match.group(1)) if match.group(2) else preview_status(match.group(1))
            self._send(200, result)
        except RequestError as exc:
            self._send(404, {"error": str(exc)})
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._send(503, {"error": "preview status unavailable", "detail": str(exc)[:500]})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        path = self.path.split("?", 1)[0]
        if path not in {"/v1/jobs", "/v1/previews"}:
            self._send(404, {"error": "not found"})
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
            result = execute_job(payload) if path == "/v1/jobs" else start_preview(payload)
            self._send(200, result)
        except (UnicodeDecodeError, json.JSONDecodeError, RequestError) as exc:
            self._send(400, {"error": str(exc)})
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._send(503, {"error": "sandbox operation unavailable", "detail": str(exc)[:500]})
        finally:
            JOB_SLOTS.release()

    def do_DELETE(self) -> None:
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        match = re.fullmatch(r"/v1/previews/([A-Za-z0-9-]{1,64})", self.path.split("?", 1)[0])
        if not match:
            self._send(404, {"error": "not found"})
            return
        try:
            self._send(200, stop_preview(match.group(1)))
        except RequestError as exc:
            self._send(404, {"error": str(exc)})
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self._send(503, {"error": "preview cleanup failed", "detail": str(exc)[:500]})


def _cleanup_stale_jobs() -> None:
    listed = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=openmanus.sandbox=true"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=10, check=True, env=_docker_env(),
    )
    ids = [item.strip() for item in listed.stdout.splitlines() if item.strip()]
    if ids:
        subprocess.run(
            ["docker", "rm", "--force", *ids], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=30, check=True, env=_docker_env(),
        )
    networks = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", "label=openmanus.sandbox=true"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=10, check=True, env=_docker_env(),
    )
    network_ids = [item.strip() for item in networks.stdout.splitlines() if item.strip()]
    if network_ids:
        subprocess.run(
            ["docker", "network", "rm", *network_ids], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=30, check=True, env=_docker_env(),
        )
    # A runner crash can leave sanitized staging copies in the shared volume.
    # They are never used by active jobs after this single-instance service
    # starts, so remove only the reserved staging prefix.
    for stale in PROJECTS_ROOT.parent.glob(".openmanus-sandbox-*"):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale, ignore_errors=True)


def main() -> None:
    if not TOKEN:
        raise SystemExit("OPENMANUS_SANDBOX_TOKEN is required")
    if not PROJECTS_ROOT.is_dir():
        raise SystemExit("OPENMANUS_SANDBOX_PROJECTS_ROOT must be an existing directory")
    _cleanup_stale_jobs()
    host = os.environ.get("OPENMANUS_SANDBOX_BIND", "127.0.0.1")
    port = int(os.environ.get("OPENMANUS_SANDBOX_PORT", "8765"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

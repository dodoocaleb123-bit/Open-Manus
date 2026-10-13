import pytest

from sandbox_runner.server import RequestError, validate_job


def test_job_schema_rejects_extra_fields_and_invalid_project():
    with pytest.raises(RequestError):
        validate_job({"project": "../secrets", "command": ["python", "-c", "print(1)"]})
    with pytest.raises(RequestError):
        validate_job({"project": "demo", "command": ["python", "-c", "print(1)"], "network": "host"})


def test_job_schema_accepts_bounded_argv_without_shell():
    project, command, timeout = validate_job({
        "project": "student-app",
        "command": ["python", "-m", "pytest", "-q"],
        "timeout_seconds": 30,
    })
    assert project == "student-app"
    assert command == ["python", "-m", "pytest", "-q"]
    assert timeout == 30


@pytest.mark.parametrize("command", [
    ["bash", "-c", "cat /etc/shadow"],
    ["npm", "exec", "malicious"],
    ["python", "--help"],
    ["python", "-c", "print(1)", "extra\x00argument"],
])
def test_job_schema_rejects_unapproved_commands(command):
    with pytest.raises(RequestError):
        validate_job({"project": "demo", "command": command})


@pytest.mark.parametrize("timeout", [0, -1, 121, True, "10"])
def test_job_schema_rejects_invalid_timeouts(timeout):
    with pytest.raises(RequestError):
        validate_job({"project": "demo", "command": ["python", "-c", "pass"], "timeout_seconds": timeout})



def test_staging_excludes_secrets_repository_state_and_symlinks(tmp_path):
    from sandbox_runner.server import _copy_workspace

    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("print('ok')", encoding="utf-8")
    (project / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (project / ".npmrc").write_text("//registry.example/:_authToken=secret", encoding="utf-8")
    (project / "private.pem").write_text("private-key", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("credential=secret", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (project / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass

    staged = tmp_path / "staged"
    manifest = _copy_workspace(project, staged)
    assert "app.py" in manifest
    assert not (staged / ".env").exists()
    assert not (staged / ".npmrc").exists()
    assert not (staged / "private.pem").exists()
    assert not (staged / ".git").exists()
    assert not (staged / "escape.txt").exists()


def test_sync_back_copies_regular_changes_but_never_secrets_or_symlinks(tmp_path):
    from sandbox_runner.server import _safe_sync_back

    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("before", encoding="utf-8")
    (project / ".env").write_text("API_KEY=keep", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "app.py").write_text("after", encoding="utf-8")
    (staged / "new.py").write_text("created", encoding="utf-8")
    (staged / ".env").write_text("API_KEY=attacker", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (staged / "escape.py").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass

    _safe_sync_back(staged, project, {"app.py": "baseline-hash"})
    assert (project / "app.py").read_text(encoding="utf-8") == "after"
    assert (project / "new.py").read_text(encoding="utf-8") == "created"
    assert (project / ".env").read_text(encoding="utf-8") == "API_KEY=keep"
    assert not (project / "escape.py").exists()



def test_runner_http_api_requires_bearer_token(tmp_path, monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from sandbox_runner import server

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(server, "TOKEN", "unit-test-token")
    monkeypatch.setattr(server, "PROJECTS_ROOT", root.resolve())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(httpd.server_address[1]) + "/health"
    try:
        request = urllib.request.Request(url)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=2)
        assert error.value.code == 401
        authorized = urllib.request.Request(url, headers={"Authorization": "Bearer unit-test-token"})
        with urllib.request.urlopen(authorized, timeout=2) as response:
            assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)



def test_runner_client_waits_longer_than_job_deadline(monkeypatch):
    import json

    import coding.runner_client as client_module
    from coding.runner_client import SandboxRunnerClient

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps({
                "job_id": "test", "returncode": 0, "stdout": "", "stderr": "",
                "duration_seconds": 1, "status": "passed",
            }).encode("utf-8")

    def fake_urlopen(_request, timeout):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    runner = SandboxRunnerClient("http://127.0.0.1:8765", "token", request_timeout=10)
    runner.run({"project": "demo", "command": ["python", "-c", "pass"], "timeout_seconds": 90})
    assert seen["timeout"] == 135



def test_output_archive_rejects_traversal_and_never_materializes_symlinks(tmp_path):
    import io
    import tarfile

    from sandbox_runner.server import _extract_workspace_archive

    destination = tmp_path / "destination"
    destination.mkdir()
    outside = tmp_path / "escaped.txt"
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        good = tarfile.TarInfo("app/generated.py")
        good_data = b"print('safe')"
        good.size = len(good_data)
        archive.addfile(good, io.BytesIO(good_data))

        traversal = tarfile.TarInfo("../escaped.txt")
        traversal_data = b"outside"
        traversal.size = len(traversal_data)
        archive.addfile(traversal, io.BytesIO(traversal_data))

        link = tarfile.TarInfo("app/escape-link")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        archive.addfile(link)

    _extract_workspace_archive(payload.getvalue(), destination)
    assert (destination / "app" / "generated.py").read_text(encoding="utf-8") == "print('safe')"
    assert not outside.exists()
    assert not (destination / "app" / "escape-link").exists()



@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires POSIX no-follow open semantics")
def test_staging_symlink_swap_cannot_read_or_chmod_host_target(tmp_path, monkeypatch):
    from sandbox_runner import server

    project = tmp_path / "project"
    project.mkdir()
    victim = project / "victim.txt"
    victim.write_text("ordinary project data", encoding="utf-8")
    secret = tmp_path / "host-secret.txt"
    secret.write_text("host secret", encoding="utf-8")
    secret.chmod(0o600)
    staged = tmp_path / "staged"
    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == victim and not swapped:
            swapped = True
            victim.unlink()
            victim.symlink_to(secret)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(server.os, "open", swap_before_open)
    server._copy_workspace(project, staged)
    assert swapped
    assert not (staged / "victim.txt").exists()
    assert secret.read_text(encoding="utf-8") == "host secret"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600

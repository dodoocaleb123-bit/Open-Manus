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

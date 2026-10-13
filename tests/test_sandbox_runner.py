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

import asyncio
import json
import threading
import urllib.request
from pathlib import Path

from api import OpenManusAPI, create_server
from execution import ControlledExecutor, SQLiteStateStore, TaskStatus
from model_adapters import (
    ControllerCommand,
    ControllerDecision,
    HealthStatus,
    ModelResponse,
    ModelRole,
)


class FakeController:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    async def decide(self, messages, *, context=None, tools=()):
        return self.decisions.pop(0)


class Adapter:
    def __init__(self, role):
        self.role = role
        self.model = f"{role.value}-test"

    async def generate(self, request):
        return ModelResponse(role=self.role, model=self.model, content="result")

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class Registry:
    def __init__(self):
        self.adapters = {role: Adapter(role) for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def run(coro):
    return asyncio.run(coro)


def test_api_creates_and_lists_persisted_task(tmp_path):
    store = SQLiteStateStore(tmp_path / "api.sqlite3")
    # A no-command completed decision exercises the API without needing a model adapter.
    controller = FakeController([ControllerDecision("Task completed.", status="completed")])
    api = OpenManusAPI(controller=controller, executor=ControlledExecutor(Registry(), store=store))
    result = run(api.create_task({"message": "Create a local project"}))
    assert result["status"] == "completed"
    listed = api.list_tasks()
    assert listed["tasks"][0]["user_request"] == "Create a local project"
    detail = api.get_task(result["task_id"])
    assert detail["messages"][0]["content"] == "Create a local project"
    assert detail["context"]["workspace_root"] == "./workspace"
    store.close()


def test_api_health_and_http_static_gui(tmp_path):
    store = SQLiteStateStore(tmp_path / "http.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=FakeController([]), executor=executor)
    server = create_server("127.0.0.1", 0, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        health = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health").read())
        assert health["status"] == "ok"
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "OPEN-MANUS" in page
        routes = urllib.request.urlopen(f"http://127.0.0.1:{port}/manus-routes.json").read().decode()
        assert "Local Control Room" in routes
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_catalog_and_real_uploaded_attachment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "upload.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=FakeController([]), executor=executor)
    task = executor.create_task("Inspect an upload")
    uploaded = api.add_uploaded_attachment(task.task_id, "notes.txt", "text/plain", b"local notes")
    path = Path(uploaded["attachment"]["path"])
    assert path.read_text() == "local notes"
    assert uploaded["attachment"]["metadata"]["uploaded"] is True
    catalog = api.catalog()
    assert catalog["agents"][0]["primary"] is True
    assert {"agents", "skills", "plugins", "scheduled_tasks", "library", "projects", "services"} <= catalog.keys()
    store.close()

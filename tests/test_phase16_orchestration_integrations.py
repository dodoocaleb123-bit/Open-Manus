import asyncio
import json
import threading
import time
import urllib.request

from api import OpenManusAPI, create_server
from automation import LocalScheduler
from execution import ControlledExecutor, SQLiteStateStore
from integrations import IntegrationGateway
from model_adapters import ModelRole
from orchestration import OrchestrationDashboard


class Registry:
    def __init__(self):
        self.adapters = {role: object() for role in ModelRole}

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def test_dashboard_keeps_deepseek_as_controller_and_summarizes_specialists(tmp_path):
    store = SQLiteStateStore(tmp_path / "dashboard.sqlite3")
    task = store.create_task("Build and research a launch page")
    store.record_activity(task.task_id, "controller_decision", {"assistant_message": "Delegating research"})
    store.record_activity(task.task_id, "delegated_call", {"role": "research", "objective": "Compare five sources"})
    store.record_activity(task.task_id, "model_result", {"role": "research", "content": "Research findings returned"})
    snapshot = OrchestrationDashboard(store).task_snapshot(task.task_id)
    assert snapshot["controller"]["id"] == "deepseek"
    assert {edge["to"] for edge in snapshot["delegation_edges"]} == {"research"}
    research = next(agent for agent in snapshot["agents"] if agent["agent_id"] == "research")
    assert research["status"] == "active"
    assert research["last_objective"] == "Compare five sources"
    store.close()


def test_scheduler_persists_and_dispatches_due_interval(tmp_path):
    scheduler = LocalScheduler(tmp_path / "schedules.sqlite3")
    item = scheduler.create({"name": "Morning report", "instruction": "Prepare the daily report", "schedule_kind": "interval", "interval_seconds": 60})
    scheduler._db.execute("UPDATE schedules SET next_run=? WHERE schedule_id=?", (time.time() - 1, item["schedule_id"]))
    scheduler._db.commit()
    dispatched = []
    result = scheduler.run_due(lambda instruction, metadata: dispatched.append((instruction, metadata)) or {"task_id": "task_123"})
    assert result[0]["status"] == "dispatched"
    assert dispatched[0][0] == "Prepare the daily report"
    assert scheduler.get(item["schedule_id"])["last_task_id"] == "task_123"
    scheduler.close()


def test_integrations_are_event_and_prepare_action_boundaries():
    gateway = IntegrationGateway()
    catalog = gateway.catalog()
    assert {item["id"] for item in catalog["integrations"]} == {"email", "calendar", "slack", "notion", "storage", "notifications", "maps", "commerce"}
    event = gateway.receive_event({"provider": "calendar", "event_type": "event.created", "payload": {"title": "Untrusted event text"}})
    assert event["route"] == "deepseek"
    assert event["payload_is_untrusted"] is True
    action = gateway.prepare_action({"provider": "slack", "action": "send_message", "details": {"channel": "general"}})
    assert action["requires_deepseek"] is True
    assert action["requires_approval"] is True


def test_api_exposes_phase16_surfaces(tmp_path):
    store = SQLiteStateStore(tmp_path / "api.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"orchestration_overview", "orchestration_task", "schedule_create", "schedule_list", "integration_catalog", "integration_receive_event", "integration_prepare_action"} <= set(executor._tools)
    schedule = api.create_schedule({"name": "Later", "instruction": "Check the local project", "schedule_kind": "once", "fire_at": time.time() + 3600})
    assert schedule["enabled"] is True
    server = create_server("127.0.0.1", 0, api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        dashboard = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard").read())
        integrations = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/integrations").read())
        schedules = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/schedules").read())
        assert dashboard["controller"] == "deepseek"
        assert len(integrations["integrations"]) == 8
        assert schedules["schedules"][0]["name"] == "Later"
    finally:
        server.shutdown()
        server.server_close()
        api.scheduler.close()
        store.close()

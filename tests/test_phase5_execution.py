import asyncio
import json

import pytest

from execution import (
    ControlledExecutor,
    ExecutionStateError,
    InMemoryStateStore,
    TaskStatus,
    ToolSpec,
)
from model_adapters import (
    CommandType,
    ControllerCommand,
    HealthStatus,
    ModelResponse,
    ModelRole,
    ModelRegistry,
)


class FakeAdapter:
    def __init__(self, role, content):
        self.role = role
        self.model = f"{role.value}-test"
        self.content = content
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return ModelResponse(role=self.role, model=self.model, content=self.content)

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class FakeRegistry:
    def __init__(self):
        self.adapters = {
            ModelRole.RESEARCH: FakeAdapter(ModelRole.RESEARCH, "research result"),
            ModelRole.CODER: FakeAdapter(ModelRole.CODER, "code result"),
            ModelRole.VISION: FakeAdapter(ModelRole.VISION, "vision result"),
            ModelRole.CREATIVE: FakeAdapter(ModelRole.CREATIVE, "creative result"),
        }

    def get(self, role):
        return self.adapters[role]


def run(coro):
    return asyncio.run(coro)


def test_delegate_executes_named_specialist_without_selecting_another_model():
    registry = FakeRegistry()
    executor = ControlledExecutor(registry)
    task = executor.create_task("Research a website")
    command = ControllerCommand(
        CommandType.DELEGATE_TO_MODEL,
        {
            "role": "research",
            "objective": "Find sources",
            "inputs": {"query": "OpenManus"},
            "expected_output": "cited findings",
        },
    )
    result = run(executor.execute(task.task_id, command))
    assert result.accepted is True
    assert result.task_status is TaskStatus.RUNNING
    assert result.output == "research result"
    assert len(registry.adapters[ModelRole.RESEARCH].requests) == 1
    assert len(registry.adapters[ModelRole.CODER].requests) == 0
    payload = json.loads(registry.adapters[ModelRole.RESEARCH].requests[0].messages[1]["content"])
    assert payload["objective"] == "Find sources"


def test_user_input_pauses_then_submission_resumes_task():
    executor = ControlledExecutor(FakeRegistry())
    task = executor.create_task("Need a platform choice")
    result = run(executor.execute(task.task_id, ControllerCommand(CommandType.REQUEST_USER_INPUT, {"question": "Web or mobile?"})))
    assert result.task_status is TaskStatus.WAITING_FOR_USER
    assert executor.store.snapshot(task.task_id).pending_input == "Web or mobile?"
    resumed = run(executor.submit_user_input(task.task_id, "Web"))
    assert resumed.task_status is TaskStatus.RUNNING
    assert executor.store.snapshot(task.task_id).last_result == "Web"


def test_approval_is_required_before_sensitive_tool_runs():
    calls = []

    def push_tool(arguments):
        calls.append(arguments)
        return "pushed"

    executor = ControlledExecutor(
        FakeRegistry(),
        tools=(ToolSpec("github_push", push_tool, approval_action="push_to_github", description="Push project"),),
    )
    task = executor.create_task("Push project")
    command = ControllerCommand(CommandType.RUN_TOOL, {"tool": "github_push", "arguments": {"branch": "main"}})
    waiting = run(executor.execute(task.task_id, command))
    assert waiting.task_status is TaskStatus.WAITING_FOR_APPROVAL
    assert calls == []
    run(executor.approve(task.task_id, "push_to_github"))
    pushed = run(executor.execute(task.task_id, command))
    assert pushed.output == "pushed"
    assert calls == [{"branch": "main"}]


def test_pause_resume_and_cancel_transitions_are_enforced():
    executor = ControlledExecutor(FakeRegistry())
    task = executor.create_task("Long job")
    paused = run(executor.execute(task.task_id, ControllerCommand(CommandType.PAUSE_TASK)))
    assert paused.task_status is TaskStatus.PAUSED
    resumed = run(executor.execute(task.task_id, ControllerCommand(CommandType.RESUME_TASK)))
    assert resumed.task_status is TaskStatus.RUNNING
    cancelled = run(executor.execute(task.task_id, ControllerCommand(CommandType.CANCEL_TASK, {"reason": "Stop now"})))
    assert cancelled.task_status is TaskStatus.CANCELLED
    with pytest.raises(ExecutionStateError, match="terminal task"):
        run(executor.execute(task.task_id, ControllerCommand(CommandType.COMPLETE_TASK)))


def test_unknown_tool_fails_task_without_falling_back_to_a_model():
    executor = ControlledExecutor(FakeRegistry())
    task = executor.create_task("Run missing tool")
    result = run(executor.execute(task.task_id, ControllerCommand(CommandType.RUN_TOOL, {"tool": "missing", "arguments": {}})))
    assert result.accepted is False
    assert result.task_status is TaskStatus.FAILED
    assert "not registered" in result.error
    assert all(not adapter.requests for adapter in executor.registry.adapters.values())


def test_state_store_tracks_command_history_and_runs():
    store = InMemoryStateStore()
    task = store.create_task("Track me")
    run_record = store.create_run(task.task_id, ControllerCommand(CommandType.COMPLETE_TASK))
    assert store.snapshot(task.task_id).command_history == ["complete_task"]
    assert store.get_run(run_record.run_id).task_id == task.task_id
    assert len(store.list_runs(task.task_id)) == 1


def test_rejecting_approval_cancels_task_and_records_rejection_without_error():
    store = InMemoryStateStore()
    executor = ControlledExecutor(FakeRegistry(), store=store)
    task = executor.create_task("Delete a generated project")
    waiting = run(executor.execute(
        task.task_id,
        ControllerCommand(CommandType.REQUEST_USER_APPROVAL, {
            "action": "delete_project",
            "summary": "Delete generated project",
        }),
    ))
    assert waiting.task_status is TaskStatus.WAITING_FOR_APPROVAL

    rejected = run(executor.reject(task.task_id, "Keep the project"))

    assert rejected.accepted is True
    assert rejected.task_status is TaskStatus.CANCELLED
    snapshot = store.snapshot(task.task_id)
    assert snapshot.pending_approval is None
    assert snapshot.last_error == "Keep the project"
    audits = store.list_audit_events(task.task_id) if hasattr(store, "list_audit_events") else []
    if audits:
        assert any(event.get("outcome") == "rejected" for event in audits if isinstance(event, dict))

import asyncio
import json

import pytest

from conversation import ConversationLoopError, DeepSeekConversationLoop
from execution import ControlledExecutor, TaskStatus, ToolSpec
from model_adapters import HealthStatus, ModelResponse, ModelRole, DeepSeekController


class SequenceAdapter:
    role = ModelRole.CONTROLLER
    model = "deepseek-r1:7b"

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if not self.decisions:
            raise AssertionError("DeepSeek was called more times than expected")
        return ModelResponse(role=self.role, model=self.model, content=self.decisions.pop(0))

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class SpecialistAdapter:
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


class Registry:
    def __init__(self):
        self.adapters = {
            role: SpecialistAdapter(role, f"{role.value} result")
            for role in (ModelRole.RESEARCH, ModelRole.CODER, ModelRole.VISION, ModelRole.CREATIVE)
        }

    def get(self, role):
        return self.adapters[role]


def decision(message, status="working", command=None):
    return json.dumps({"assistant_message": message, "status": status, "command": command})


def command(command_type, arguments):
    return {"type": command_type, "arguments": arguments}


def run(coro):
    return asyncio.run(coro)


def make_loop(decisions, *, tools=(), max_turns=10, events=None):
    controller_adapter = SequenceAdapter(decisions)
    controller = DeepSeekController(controller_adapter)
    loop = DeepSeekConversationLoop(
        controller,
        ControlledExecutor(Registry(), tools=tools),
        max_turns=max_turns,
        event_sink=events.append if events is not None else None,
    )
    return loop, controller_adapter


def test_loop_delegates_then_returns_specialist_result_to_deepseek():
    loop, controller_adapter = make_loop(
        [
            decision(
                "I will research this first.",
                command=command(
                    "delegate_to_model",
                    {"role": "research", "objective": "Find sources", "expected_output": "cited findings"},
                ),
            ),
            decision("The research is complete.", status="completed", command=command("complete_task", {})),
        ]
    )
    result = run(loop.start("Research this topic"))
    assert result.status is TaskStatus.COMPLETED
    assert result.turns == 2
    assert len(controller_adapter.requests) == 2
    assert any("research result" in str(message) for message in result.messages)
    assert [event.kind for event in result.events].count("assistant_message") == 2


def test_loop_pauses_for_input_and_continues_after_user_reply():
    loop, _ = make_loop(
        [
            decision("I need one choice.", status="waiting_for_user", command=command("request_user_input", {"question": "Which platform?"})),
            decision("I can finish now.", status="completed", command=command("complete_task", {})),
        ]
    )
    waiting = run(loop.start("Build an app"))
    assert waiting.status is TaskStatus.WAITING_FOR_USER
    finished = run(loop.provide_user_input(waiting.task_id, "Laptop"))
    assert finished.status is TaskStatus.COMPLETED
    assert any("Laptop" in str(message) for message in finished.messages)


def test_loop_requires_approval_then_replays_the_exact_pending_command():
    calls = []

    def push(arguments):
        calls.append(arguments)
        return "pushed"

    loop, _ = make_loop(
        [
            decision("I need approval to push.", status="waiting_for_approval", command=command("run_tool", {"tool": "push", "arguments": {"branch": "main"}})),
            decision("The push completed.", status="completed", command=command("complete_task", {})),
        ],
        tools=(ToolSpec("push", push, approval_action="push_to_github", description="Push project"),),
    )
    waiting = run(loop.start("Push project"))
    assert waiting.status is TaskStatus.WAITING_FOR_APPROVAL
    finished = run(loop.approve(waiting.task_id, "push_to_github"))
    assert finished.status is TaskStatus.COMPLETED
    assert calls == [{"branch": "main"}]


def test_loop_stops_safely_at_turn_limit():
    loop, _ = make_loop(
        [decision("Still working.", command=command("pause_task", {}))],
        max_turns=1,
    )
    result = run(loop.start("Keep going"))
    assert result.status is TaskStatus.PAUSED


def test_loop_cancellation_is_available_for_waiting_task():
    loop, _ = make_loop(
        [decision("I need input.", command=command("request_user_input", {"question": "More?"}))]
    )
    waiting = run(loop.start("Do it"))
    cancelled = run(loop.cancel(waiting.task_id, "User changed their mind"))
    assert cancelled.status is TaskStatus.CANCELLED


def test_resuming_unknown_or_non_loop_task_is_rejected():
    loop, _ = make_loop([])
    with pytest.raises(ConversationLoopError, match="context is not available"):
        run(loop.provide_user_input("task_missing", "answer"))



def test_loop_marks_controller_timeout_as_failed_without_hanging():
    class HangingAdapter(SequenceAdapter):
        async def generate(self, request):
            await asyncio.sleep(1)
            return await super().generate(request)

    adapter = HangingAdapter([decision("This should not be reached.", status="completed", command=command("complete_task", {}))])
    loop = DeepSeekConversationLoop(
        DeepSeekController(adapter),
        ControlledExecutor(Registry()),
        model_timeout_seconds=0.01,
    )
    result = run(loop.start("Test timeout"))
    assert result.status is TaskStatus.FAILED
    assert "timed out" in (result.error or "")
    assert any(event.kind == "error" for event in result.events)


def test_loop_reports_malformed_controller_output_as_failed():
    adapter = SequenceAdapter(["not valid JSON"])
    loop = DeepSeekConversationLoop(
        DeepSeekController(adapter),
        ControlledExecutor(Registry()),
        model_timeout_seconds=1,
    )
    result = run(loop.start("Test malformed response"))
    assert result.status is TaskStatus.FAILED
    assert "Controller request failed" in (result.error or "")



def test_loop_refuses_automatic_retry_of_non_idempotent_tool():
    calls = []
    loop, _ = make_loop(
        [decision("Run the external action.", command=command(
            "run_tool", {"tool": "external", "arguments": {}}
        ))],
        tools=(ToolSpec("external", lambda args: calls.append(args) or "done"),),
    )
    failed = run(loop.start("Run external action"))
    assert failed.status is TaskStatus.FAILED
    with pytest.raises(ConversationLoopError, match="Automatic retry refused"):
        run(loop.retry(failed.task_id))
    assert calls == []

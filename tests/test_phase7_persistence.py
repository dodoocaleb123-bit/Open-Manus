import asyncio
import json

from conversation import DeepSeekConversationLoop
from execution import (
    ControlledExecutor,
    RunStatus,
    SQLiteStateStore,
    TaskStatus,
    ToolSpec,
)
from model_adapters import (
    CommandType,
    ControllerCommand,
    DeepSeekController,
    HealthStatus,
    ModelResponse,
    ModelRole,
    PlatformContext,
)


class SequenceAdapter:
    role = ModelRole.CONTROLLER
    model = "deepseek-r1:7b"

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return ModelResponse(role=self.role, model=self.model, content=self.responses.pop(0))

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class SpecialistAdapter:
    def __init__(self, role):
        self.role = role
        self.model = f"{role.value}-test"

    async def generate(self, request):
        return ModelResponse(role=self.role, model=self.model, content="specialist result")

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class Registry:
    def get(self, role):
        return SpecialistAdapter(role)


def response(message, status, command):
    return json.dumps({"assistant_message": message, "status": status, "command": command})


def cmd(kind, arguments):
    return {"type": kind, "arguments": arguments}


def run(coro):
    return asyncio.run(coro)


def test_sqlite_store_round_trips_tasks_runs_and_context(tmp_path):
    database = tmp_path / "openmanus.sqlite3"
    store = SQLiteStateStore(database)
    task = store.create_task("Persist this task")
    context = PlatformContext(workspace_root="/tmp/project", attachments=("diagram.png",))
    store.save_context(task.task_id, context)
    run_record = store.create_run(task.task_id, ControllerCommand(CommandType.COMPLETE_TASK))
    store.finish_run(run_record.run_id, RunStatus.COMPLETED, result="done")
    store.update_task(task.task_id, status=TaskStatus.COMPLETED, last_result="done")
    store.record_attachment(task.task_id, "att-1", "/tmp/diagram.png", "image/png")
    store.record_artifact(task.task_id, "art-1", "/tmp/report.md", "report")
    store.record_activity(task.task_id, "plan", {"steps": ["solve"]})
    store.close()

    restored = SQLiteStateStore(database)
    assert restored.snapshot(task.task_id).status is TaskStatus.COMPLETED
    assert restored.snapshot(task.task_id).last_result == "done"
    assert restored.get_run(run_record.run_id).command.type is CommandType.COMPLETE_TASK
    assert restored.load_context(task.task_id).workspace_root == "/tmp/project"
    assert restored.list_attachments(task.task_id)[0]["media_type"] == "image/png"
    assert restored.list_artifacts(task.task_id)[0]["artifact_type"] == "report"
    assert restored.list_activity(task.task_id, "plan")[0]["payload"]["steps"] == ["solve"]
    restored.close()


def test_conversation_can_resume_after_process_restart(tmp_path):
    database = tmp_path / "resume.sqlite3"
    first_store = SQLiteStateStore(database)
    first_adapter = SequenceAdapter(
        [
            response("I need your choice.", "waiting_for_user", cmd("request_user_input", {"question": "Which OS?"}))
        ]
    )
    first_loop = DeepSeekConversationLoop(
        DeepSeekController(first_adapter),
        ControlledExecutor(Registry(), store=first_store),
    )
    waiting = run(first_loop.start("Prepare the local setup", context=PlatformContext(workspace_root="/laptop")))
    assert waiting.status is TaskStatus.WAITING_FOR_USER
    task_id = waiting.task_id
    first_store.close()

    second_store = SQLiteStateStore(database)
    second_adapter = SequenceAdapter(
        [response("Setup is complete.", "completed", cmd("complete_task", {}))]
    )
    second_loop = DeepSeekConversationLoop(
        DeepSeekController(second_adapter),
        ControlledExecutor(Registry(), store=second_store),
    )
    finished = run(second_loop.provide_user_input(task_id, "Linux"))
    assert finished.status is TaskStatus.COMPLETED
    assert finished.messages[0]["content"] == "Prepare the local setup"
    assert any("Linux" in str(message) for message in finished.messages)
    assert "Workspace: /laptop" in second_adapter.requests[0].messages[0]["content"]
    assert len(second_store.list_events(task_id)) >= 3
    second_store.close()


def test_executor_persists_delegation_and_tool_activity(tmp_path):
    store = SQLiteStateStore(tmp_path / "activity.sqlite3")
    calls = []

    def tool(arguments):
        calls.append(arguments)
        return "tool result"

    executor = ControlledExecutor(
        Registry(),
        store=store,
        tools=(ToolSpec("local_tool", tool),),
    )
    task = executor.create_task("Run local work")
    delegated = run(
        executor.execute(
            task.task_id,
            ControllerCommand(
                CommandType.DELEGATE_TO_MODEL,
                {"role": "research", "objective": "Find facts", "expected_output": "findings"},
            ),
        )
    )
    tool_result = run(executor.execute(task.task_id, ControllerCommand(CommandType.RUN_TOOL, {"tool": "local_tool", "arguments": {"x": 1}})))
    activity_types = [entry["type"] for entry in store.list_activity(task.task_id)]
    assert delegated.output == "specialist result"
    assert tool_result.output == "tool result"
    assert calls == [{"x": 1}]
    assert "delegated_call" in activity_types
    assert "model_result" in activity_types
    assert "tool_activity" in activity_types
    store.close()

"""Non-intelligent executor for commands selected by DeepSeek."""
from __future__ import annotations

import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from model_adapters import (
    ControllerCommand,
    CommandType,
    ModelAdapter,
    ModelRegistry,
    ModelRequest,
    ModelRole,
)

from .store import ExecutionStateError, InMemoryStateStore
from .types import CommandRun, ExecutionResult, RunStatus, TaskRecord, TaskStatus

ToolHandler = Callable[[Mapping[str, Any]], Any | Awaitable[Any]]


class ExecutionError(RuntimeError):
    """Raised for invalid execution requests or unavailable resources."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: ToolHandler
    approval_action: str | None = None
    description: str = ""


class ControlledExecutor:
    """Execute exactly the command DeepSeek emitted.

    This class never classifies a user request and never picks a specialist. A
    `delegate_to_model` command names its target role; this executor only looks
    up that named role in the registry and returns the result to the caller.
    """

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        store: InMemoryStateStore | None = None,
        tools: tuple[ToolSpec, ...] = (),
    ) -> None:
        self.registry = registry
        self.store = store or InMemoryStateStore()
        self._tools = {tool.name: tool for tool in tools}

    def create_task(self, user_request: str) -> TaskRecord:
        return self.store.create_task(user_request)

    def register_tool(self, tool: ToolSpec) -> None:
        if not tool.name.strip():
            raise ExecutionError("Tool name must not be empty")
        self._tools[tool.name] = tool

    async def execute(self, task_id: str, command: ControllerCommand) -> ExecutionResult:
        task = self.store.get_task(task_id)
        self._ensure_command_allowed(task, command.type)
        run = self.store.create_run(task_id, command)
        self._record_activity(task_id, "command", command.to_dict())
        try:
            result = await self._dispatch(task, run, command)
            return result
        except Exception as exc:
            self._record_activity(task_id, "error", {"command": command.to_dict(), "error": str(exc)})
            self.store.finish_run(run.run_id, RunStatus.FAILED, error=str(exc))
            self.store.update_task(task_id, status=TaskStatus.FAILED, last_error=str(exc))
            return ExecutionResult(
                task_id=task_id,
                run_id=run.run_id,
                accepted=False,
                task_status=TaskStatus.FAILED,
                error=str(exc),
            )

    async def approve(self, task_id: str, action: str) -> ExecutionResult:
        task = self.store.get_task(task_id)
        pending = task.pending_approval
        if task.status is not TaskStatus.WAITING_FOR_APPROVAL or not pending:
            raise ExecutionStateError("Task is not waiting for approval")
        if pending.get("action") != action:
            raise ExecutionStateError("Approval action does not match the pending request")
        self.store.update_task(
            task_id,
            status=TaskStatus.RUNNING,
            pending_approval=None,
            approved_actions=task.approved_actions | {action},
        )
        self._record_activity(task_id, "approval", {"action": action, "approved": True})
        return ExecutionResult(task_id, task.current_run_id, True, TaskStatus.RUNNING, output={"approved": action})

    async def reject(self, task_id: str, reason: str = "User rejected the approval request") -> ExecutionResult:
        task = self.store.get_task(task_id)
        if task.status is not TaskStatus.WAITING_FOR_APPROVAL:
            raise ExecutionStateError("Task is not waiting for approval")
        self.store.update_task(task_id, status=TaskStatus.CANCELLED, pending_approval=None, last_error=reason)
        if task.current_run_id:
            self.store.finish_run(task.current_run_id, RunStatus.CANCELLED, error=reason)
        self._record_activity(task_id, "approval", {"approved": False, "reason": reason})
        return ExecutionResult(task_id, task.current_run_id, True, TaskStatus.CANCELLED, output={"rejected": True})

    async def submit_user_input(self, task_id: str, value: str) -> ExecutionResult:
        task = self.store.get_task(task_id)
        if task.status is not TaskStatus.WAITING_FOR_USER:
            raise ExecutionStateError("Task is not waiting for user input")
        if not value.strip():
            raise ExecutionStateError("User input must not be empty")
        self.store.update_task(task_id, status=TaskStatus.RUNNING, pending_input=None, last_result=value)
        if task.current_run_id:
            self.store.finish_run(task.current_run_id, RunStatus.COMPLETED, result=value)
        self._record_activity(task_id, "user_input", {"value": value})
        return ExecutionResult(task_id, task.current_run_id, True, TaskStatus.RUNNING, output=value)

    async def _dispatch(self, task: TaskRecord, run: CommandRun, command: ControllerCommand) -> ExecutionResult:
        command_type = command.type
        if command_type is CommandType.DELEGATE_TO_MODEL:
            return await self._delegate(task, run, command)
        if command_type is CommandType.RUN_TOOL:
            return await self._run_tool(task, run, command)
        if command_type is CommandType.REQUEST_USER_INPUT:
            return self._wait_for_input(task, run, command)
        if command_type is CommandType.REQUEST_USER_APPROVAL:
            return self._wait_for_approval(task, run, command)
        if command_type is CommandType.PAUSE_TASK:
            return self._transition(task, run, TaskStatus.PAUSED, RunStatus.WAITING, "Task paused")
        if command_type is CommandType.RESUME_TASK:
            return self._resume(task, run)
        if command_type is CommandType.RETRY_TASK:
            return self._transition(task, run, TaskStatus.RUNNING, RunStatus.COMPLETED, "Retry requested")
        if command_type is CommandType.CANCEL_TASK:
            return self._transition(task, run, TaskStatus.CANCELLED, RunStatus.CANCELLED, command.arguments["reason"])
        if command_type is CommandType.COMPLETE_TASK:
            return self._transition(task, run, TaskStatus.COMPLETED, RunStatus.COMPLETED, "Task completed")
        raise ExecutionError(f"Unsupported command: {command_type}")

    async def _delegate(self, task: TaskRecord, run: CommandRun, command: ControllerCommand) -> ExecutionResult:
        role = ModelRole(str(command.arguments["role"]))
        adapter = self.registry.get(role)
        objective = str(command.arguments["objective"])
        inputs = command.arguments.get("inputs", {})
        expected_output = str(command.arguments["expected_output"])
        request = ModelRequest(
            messages=(
                {
                    "role": "system",
                    "content": (
                        f"You are the {role.value} specialist. Perform only the assigned subtask and return your result to DeepSeek. "
                        "Do not address the user as the primary assistant."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"objective": objective, "inputs": inputs, "expected_output": expected_output},
                        ensure_ascii=False,
                    ),
                },
            ),
            metadata={"task_id": task.task_id, "run_id": run.run_id, "delegated_by": "deepseek"},
        )
        self._record_activity(task.task_id, "delegated_call", {"role": role.value, "objective": objective, "run_id": run.run_id})
        response = await adapter.generate(request)
        if role is ModelRole.VISION:
            output = self._structured_vision_findings(response.content)
        elif role is ModelRole.RESEARCH and ("sources" in inputs or "citations" in inputs):
            output = self._structured_research_findings(response.content, inputs)
        else:
            output = response.content
        self._record_activity(task.task_id, "model_result", {"role": role.value, "run_id": run.run_id, "content": output})
        self.store.finish_run(run.run_id, RunStatus.COMPLETED, result=output)
        self.store.update_task(task.task_id, status=TaskStatus.RUNNING, last_result=output, last_error=None)
        return ExecutionResult(task.task_id, run.run_id, True, TaskStatus.RUNNING, output=output)

    async def _run_tool(self, task: TaskRecord, run: CommandRun, command: ControllerCommand) -> ExecutionResult:
        name = str(command.arguments["tool"])
        spec = self._tools.get(name)
        if spec is None:
            raise ExecutionError(f"Tool is not registered: {name}")
        if spec.approval_action and spec.approval_action not in task.approved_actions:
            pending = {
                "action": spec.approval_action,
                "summary": spec.description or f"Run tool {name}",
                "impact": "This tool requires explicit user approval before execution.",
            }
            self.store.finish_run(run.run_id, RunStatus.WAITING, result=pending)
            self.store.update_task(task.task_id, status=TaskStatus.WAITING_FOR_APPROVAL, pending_approval=pending)
            return ExecutionResult(task.task_id, run.run_id, True, TaskStatus.WAITING_FOR_APPROVAL, output=pending, waiting_for="approval")
        if spec.approval_action:
            task.approved_actions.discard(spec.approval_action)
        value = spec.handler(command.arguments["arguments"])
        if inspect.isawaitable(value):
            value = await value
        self._record_activity(task.task_id, "tool_activity", {"tool": name, "arguments": command.arguments["arguments"], "result": value})
        self.store.finish_run(run.run_id, RunStatus.COMPLETED, result=value)
        self.store.update_task(task.task_id, status=TaskStatus.RUNNING, last_result=value, last_error=None)
        return ExecutionResult(task.task_id, run.run_id, True, TaskStatus.RUNNING, output=value)

    def _wait_for_input(self, task: TaskRecord, run: CommandRun, command: ControllerCommand) -> ExecutionResult:
        question = str(command.arguments["question"])
        self.store.finish_run(run.run_id, RunStatus.WAITING, result=question)
        self.store.update_task(task.task_id, status=TaskStatus.WAITING_FOR_USER, pending_input=question)
        return ExecutionResult(task.task_id, run.run_id, True, TaskStatus.WAITING_FOR_USER, output=question, waiting_for="user_input")

    def _wait_for_approval(self, task: TaskRecord, run: CommandRun, command: ControllerCommand) -> ExecutionResult:
        pending = dict(command.arguments)
        self.store.finish_run(run.run_id, RunStatus.WAITING, result=pending)
        self.store.update_task(task.task_id, status=TaskStatus.WAITING_FOR_APPROVAL, pending_approval=pending)
        return ExecutionResult(task.task_id, run.run_id, True, TaskStatus.WAITING_FOR_APPROVAL, output=pending, waiting_for="approval")

    def _resume(self, task: TaskRecord, run: CommandRun) -> ExecutionResult:
        if task.status is not TaskStatus.PAUSED:
            raise ExecutionStateError("Only a paused task can be resumed")
        return self._transition(task, run, TaskStatus.RUNNING, RunStatus.COMPLETED, "Task resumed")

    def _transition(self, task: TaskRecord, run: CommandRun, status: TaskStatus, run_status: RunStatus, output: Any) -> ExecutionResult:
        self.store.finish_run(run.run_id, run_status, result=output)
        self.store.update_task(task.task_id, status=status, last_result=output, last_error=None)
        return ExecutionResult(task.task_id, run.run_id, True, status, output=output)

    def _record_activity(self, task_id: str, activity_type: str, payload: Mapping[str, Any]) -> None:
        record_activity = getattr(self.store, "record_activity", None)
        if record_activity is not None:
            record_activity(task_id, activity_type, payload)

    @staticmethod
    def _structured_vision_findings(content: str) -> str:
        try:
            parsed = json.loads(content)
            findings = parsed if isinstance(parsed, dict) else {"raw": parsed}
        except json.JSONDecodeError:
            findings = {"raw": content}
        return json.dumps(
            {"specialist": "gemma", "analysis_type": "visual_attachment", "findings": findings},
            ensure_ascii=False,
        )

    @staticmethod
    def _structured_research_findings(content: str, inputs: Mapping[str, Any]) -> str:
        try:
            parsed = json.loads(content)
            findings = parsed if isinstance(parsed, dict) else {"summary": parsed}
        except json.JSONDecodeError:
            findings = {"summary": content}
        citations = inputs.get("citations", [])
        if not isinstance(citations, list):
            citations = list(citations) if isinstance(citations, tuple) else []
        return json.dumps(
            {
                "specialist": "qwen2.5:3b",
                "analysis_type": "research_evidence_review",
                "findings": findings,
                "citations": citations,
                "source_count": len(inputs.get("sources", [])) if isinstance(inputs.get("sources", []), list) else 0,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _ensure_command_allowed(task: TaskRecord, command_type: CommandType) -> None:
        if task.status in {TaskStatus.COMPLETED, TaskStatus.CANCELLED} and command_type not in {CommandType.RESUME_TASK, CommandType.RETRY_TASK}:
            raise ExecutionStateError(f"Cannot execute {command_type.value} for terminal task {task.task_id}")
        if task.status is TaskStatus.WAITING_FOR_USER and command_type is not CommandType.CANCEL_TASK:
            raise ExecutionStateError("Task is waiting for user input")
        if task.status is TaskStatus.WAITING_FOR_APPROVAL and command_type not in {CommandType.CANCEL_TASK}:
            raise ExecutionStateError("Task is waiting for approval")

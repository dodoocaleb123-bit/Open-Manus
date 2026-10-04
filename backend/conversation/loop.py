"""DeepSeek conversation loop for local interactive execution."""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from execution import ControlledExecutor, ExecutionResult, TaskStatus
from model_adapters import (
    CommandType,
    ControllerCommand,
    ControllerDecision,
    DeepSeekController,
    PlatformContext,
)

EventSink = Callable[["ConversationEvent"], Any | Awaitable[Any]]


@dataclass(frozen=True)
class ConversationEvent:
    kind: str
    task_id: str
    message: str
    status: TaskStatus
    command_type: str | None = None
    output: Any = None


@dataclass(frozen=True)
class ConversationResult:
    task_id: str
    status: TaskStatus
    final_message: str | None
    turns: int
    events: tuple[ConversationEvent, ...] = field(default_factory=tuple)
    messages: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    error: str | None = None


class ConversationLoopError(RuntimeError):
    """Raised when a conversation cannot be continued safely."""


class DeepSeekConversationLoop:
    """Drive the DeepSeek -> executor -> DeepSeek control cycle.

    DeepSeek remains the planner. This class only preserves conversation context,
    dispatches the one validated command DeepSeek selected, and returns the
    execution result to DeepSeek for the next decision.
    """

    def __init__(
        self,
        controller: DeepSeekController,
        executor: ControlledExecutor,
        *,
        max_turns: int = 20,
        event_sink: EventSink | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self.controller = controller
        self.executor = executor
        self.max_turns = max_turns
        self.event_sink = event_sink
        self._messages: dict[str, list[Mapping[str, Any]]] = {}
        self._events: dict[str, list[ConversationEvent]] = {}
        self._turns: dict[str, int] = {}
        self._contexts: dict[str, PlatformContext] = {}

    async def start(
        self,
        user_message: str,
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ConversationResult:
        if not user_message.strip():
            raise ConversationLoopError("user_message must not be empty")
        task = self.executor.create_task(user_message)
        effective_context = context or PlatformContext()
        self._messages[task.task_id] = []
        self._events[task.task_id] = []
        self._turns[task.task_id] = 0
        self._contexts[task.task_id] = effective_context
        self._append_message(task.task_id, {"role": "user", "content": user_message})
        self._save_context(task.task_id, effective_context)
        return await self._drive(task.task_id, context=effective_context, tools=tools)

    async def provide_user_input(
        self,
        task_id: str,
        value: str,
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ConversationResult:
        self._require_conversation(task_id)
        context = context or self._contexts.get(task_id) or self._load_context(task_id) or PlatformContext()
        result = await self.executor.submit_user_input(task_id, value)
        self._append_message(task_id, {"role": "user", "content": f"User provided the requested input: {value}"})
        await self._record_execution(task_id, result)
        return await self._drive(task_id, context=context, tools=tools)

    async def approve(
        self,
        task_id: str,
        action: str,
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ConversationResult:
        self._require_conversation(task_id)
        context = context or self._contexts.get(task_id) or self._load_context(task_id) or PlatformContext()
        approval = await self.executor.approve(task_id, action)
        self._append_message(task_id, {"role": "user", "content": f"User approved the action: {action}"})
        await self._record_execution(task_id, approval)
        pending_run = self.executor.store.get_run(approval.run_id) if approval.run_id else None
        if pending_run is None:
            raise ConversationLoopError("Approved task has no pending command to resume")
        replay = await self.executor.execute(task_id, pending_run.command)
        await self._record_execution(task_id, replay)
        self._append_message(task_id, {"role": "user", "content": self._execution_message(pending_run.command, replay)})
        if replay.task_status is not TaskStatus.RUNNING:
            return self._result(task_id)
        return await self._drive(task_id, context=context, tools=tools)

    async def cancel(self, task_id: str, reason: str = "Cancelled by user") -> ConversationResult:
        self._require_conversation(task_id)
        command = ControllerCommand(CommandType.CANCEL_TASK, {"reason": reason})
        result = await self.executor.execute(task_id, command)
        await self._record_execution(task_id, result)
        self._append_message(task_id, {"role": "user", "content": self._execution_message(command, result)})
        return self._result(task_id)

    async def retry(
        self,
        task_id: str,
        *,
        context: PlatformContext | None = None,
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> ConversationResult:
        """Retry the most recent failed command, then return control to DeepSeek."""
        self._require_conversation(task_id)
        context = context or self._contexts.get(task_id) or self._load_context(task_id) or PlatformContext()
        failed_runs = [run for run in self.executor.store.list_runs(task_id) if run.error]
        if not failed_runs:
            raise ConversationLoopError("Task has no failed command to retry")
        original = failed_runs[-1].command
        resumed = await self.executor.execute(task_id, ControllerCommand(CommandType.RETRY_TASK))
        await self._record_execution(task_id, resumed)
        replay = await self.executor.execute(task_id, original)
        await self._record_execution(task_id, replay)
        self._append_message(task_id, {"role": "user", "content": self._execution_message(original, replay)})
        if replay.task_status is not TaskStatus.RUNNING:
            return self._result(task_id)
        return await self._drive(task_id, context=context, tools=tools)

    async def _drive(
        self,
        task_id: str,
        *,
        context: PlatformContext | None,
        tools: Sequence[Mapping[str, Any]],
    ) -> ConversationResult:
        self._require_conversation(task_id)
        context = context or self._contexts.get(task_id) or self._load_context(task_id) or PlatformContext()
        self._contexts[task_id] = context
        self._save_context(task_id, context)
        while self._turns[task_id] < self.max_turns:
            task = self.executor.store.snapshot(task_id)
            if task.status is not TaskStatus.RUNNING:
                return self._result(task_id)
            self._turns[task_id] += 1
            decision = await self.controller.decide(self._messages[task_id], context=context, tools=tools)
            await self._record_decision(task_id, decision)
            self._append_message(
                task_id,
                {"role": "assistant", "content": json.dumps(decision.to_dict(), ensure_ascii=False)},
            )
            command = decision.command
            if command is None:
                if decision.status == "completed":
                    command = ControllerCommand(CommandType.COMPLETE_TASK)
                else:
                    self._append_message(
                        task_id,
                        {"role": "user", "content": "No command was executed. Decide the next step or complete the task."},
                    )
                    continue
            result = await self.executor.execute(task_id, command)
            await self._record_execution(task_id, result)
            self._append_message(task_id, {"role": "user", "content": self._execution_message(command, result)})
            if result.task_status is not TaskStatus.RUNNING:
                return self._result(task_id)

        self.executor.store.update_task(
            task_id,
            status=TaskStatus.FAILED,
            last_error=f"Conversation exceeded max_turns={self.max_turns}",
        )
        return self._result(task_id, error=f"Conversation exceeded max_turns={self.max_turns}")

    async def _record_decision(self, task_id: str, decision: ControllerDecision) -> None:
        task = self.executor.store.snapshot(task_id)
        event = ConversationEvent(
            kind="assistant_message",
            task_id=task_id,
            message=decision.assistant_message,
            status=task.status,
            command_type=decision.command.type.value if decision.command else None,
        )
        record_activity = getattr(self.executor.store, "record_activity", None)
        if record_activity is not None:
            record_activity(task_id, "controller_decision", decision.to_dict())
        await self._emit(task_id, event)

    async def _record_execution(self, task_id: str, result: ExecutionResult) -> None:
        event = ConversationEvent(
            kind="execution_result",
            task_id=task_id,
            message=result.error or "Command execution returned",
            status=result.task_status,
            output=result.output,
        )
        await self._emit(task_id, event)

    async def _emit(self, task_id: str, event: ConversationEvent) -> None:
        self._events[task_id].append(event)
        append_event = getattr(self.executor.store, "append_event", None)
        if append_event is not None:
            append_event(event)
        if self.event_sink is not None:
            value = self.event_sink(event)
            if inspect.isawaitable(value):
                await value

    def _result(self, task_id: str, error: str | None = None) -> ConversationResult:
        task = self.executor.store.snapshot(task_id)
        final_message = next(
            (event.message for event in reversed(self._events[task_id]) if event.kind == "assistant_message"),
            None,
        )
        return ConversationResult(
            task_id=task_id,
            status=task.status,
            final_message=final_message,
            turns=self._turns[task_id],
            events=tuple(self._events[task_id]),
            messages=tuple(self._messages[task_id]),
            error=error or task.last_error,
        )

    def _require_conversation(self, task_id: str) -> None:
        if task_id in self._messages:
            return
        list_messages = getattr(self.executor.store, "list_messages", None)
        if list_messages is None:
            raise ConversationLoopError("Conversation context is not available in this process")
        self._messages[task_id] = list(list_messages(task_id))
        list_events = getattr(self.executor.store, "list_events", None)
        persisted_events = list_events(task_id) if list_events is not None else ()
        self._events[task_id] = [
            ConversationEvent(
                kind=item["kind"],
                task_id=task_id,
                message=item["message"],
                status=TaskStatus(item["status"]),
                command_type=item.get("command_type"),
                output=item.get("output"),
            )
            for item in persisted_events
        ]
        self._turns[task_id] = sum(1 for message in self._messages[task_id] if message.get("role") == "assistant")
        loaded_context = self._load_context(task_id)
        if loaded_context is not None:
            self._contexts[task_id] = loaded_context

    def _append_message(self, task_id: str, message: Mapping[str, Any]) -> None:
        self._messages[task_id].append(message)
        append_message = getattr(self.executor.store, "append_message", None)
        if append_message is not None:
            append_message(task_id, message)

    def _save_context(self, task_id: str, context: PlatformContext) -> None:
        save_context = getattr(self.executor.store, "save_context", None)
        if save_context is not None:
            save_context(task_id, context)

    def _load_context(self, task_id: str) -> PlatformContext | None:
        load_context = getattr(self.executor.store, "load_context", None)
        return load_context(task_id) if load_context is not None else None

    @staticmethod
    def _execution_message(command: ControllerCommand, result: ExecutionResult) -> str:
        payload = {
            "execution_result": {
                "command": command.to_dict(),
                "accepted": result.accepted,
                "task_status": result.task_status.value,
                "output": result.output,
                "error": result.error,
                "waiting_for": result.waiting_for,
            }
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

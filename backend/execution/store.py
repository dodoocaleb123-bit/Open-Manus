"""In-memory state store; a persistent store can implement the same boundary later."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace
from typing import Any

from model_adapters import ControllerCommand

from .types import CommandRun, TaskRecord


class ExecutionStateError(RuntimeError):
    """Raised when a task state transition is invalid."""


class InMemoryStateStore:
    """Small, thread-safe state store for local Phase 5 execution."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._runs: dict[str, CommandRun] = {}
        self._lock = threading.RLock()

    def create_task(self, user_request: str) -> TaskRecord:
        if not user_request.strip():
            raise ExecutionStateError("user_request must not be empty")
        now = time.time()
        task = TaskRecord(task_id=f"task_{uuid.uuid4().hex}", user_request=user_request, created_at=now, updated_at=now)
        with self._lock:
            self._tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as exc:
                raise ExecutionStateError(f"Unknown task: {task_id}") from exc

    def create_run(self, task_id: str, command: ControllerCommand) -> CommandRun:
        self.get_task(task_id)
        now = time.time()
        run = CommandRun(
            run_id=f"run_{uuid.uuid4().hex}",
            task_id=task_id,
            command=command,
            started_at=now,
        )
        with self._lock:
            self._runs[run.run_id] = run
            task = self._tasks[task_id]
            task.current_run_id = run.run_id
            task.command_history.append(command.type.value)
            task.updated_at = now
        return run

    def get_run(self, run_id: str) -> CommandRun:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise ExecutionStateError(f"Unknown command run: {run_id}") from exc

    def update_task(self, task_id: str, **changes: Any) -> TaskRecord:
        with self._lock:
            task = self.get_task(task_id)
            for key, value in changes.items():
                if not hasattr(task, key):
                    raise ExecutionStateError(f"Unknown task field: {key}")
                setattr(task, key, value)
            task.updated_at = time.time()
            return task

    def finish_run(self, run_id: str, status, *, result: Any = None, error: str | None = None) -> CommandRun:
        with self._lock:
            run = self.get_run(run_id)
            run.status = status
            run.result = result
            run.error = error
            run.finished_at = time.time()
            return run

    def snapshot(self, task_id: str) -> TaskRecord:
        """Return a detached copy suitable for API responses."""
        with self._lock:
            task = self.get_task(task_id)
            return replace(task, approved_actions=set(task.approved_actions), command_history=list(task.command_history))

    def list_runs(self, task_id: str) -> tuple[CommandRun, ...]:
        with self._lock:
            return tuple(run for run in self._runs.values() if run.task_id == task_id)

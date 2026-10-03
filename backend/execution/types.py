"""State records used by the Phase 5 controlled execution layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from model_adapters import ControllerCommand


class TaskStatus(StrEnum):
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskRecord:
    task_id: str
    user_request: str
    status: TaskStatus = TaskStatus.RUNNING
    current_run_id: str | None = None
    pending_input: str | None = None
    pending_approval: Mapping[str, Any] | None = None
    approved_actions: set[str] = field(default_factory=set)
    last_result: Any = None
    last_error: str | None = None
    command_history: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class CommandRun:
    run_id: str
    task_id: str
    command: ControllerCommand
    status: RunStatus = RunStatus.RUNNING
    result: Any = None
    error: str | None = None
    started_at: float = 0.0
    finished_at: float | None = None


@dataclass(frozen=True)
class ExecutionResult:
    task_id: str
    run_id: str | None
    accepted: bool
    task_status: TaskStatus
    output: Any = None
    error: str | None = None
    waiting_for: str | None = None

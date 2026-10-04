"""Durable SQLite state and context storage for local Open-Manus deployments."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from model_adapters import CommandType, ControllerCommand, PlatformContext

from .store import InMemoryStateStore
from .types import CommandRun, RunStatus, TaskRecord, TaskStatus


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class SQLiteStateStore(InMemoryStateStore):
    """Thread-safe state store that mirrors records into a local SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        super().__init__()
        self.database_path = str(database_path)
        path = Path(self.database_path)
        if self.database_path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.database_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db_lock = threading.RLock()
        self._create_schema()
        self._load_state()

    @classmethod
    def from_environment(cls) -> "SQLiteStateStore":
        return cls(os.getenv("OPENMANUS_STATE_DB", "./workspace/openmanus.sqlite3"))

    def _create_schema(self) -> None:
        with self._db_lock:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    user_request TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_run_id TEXT,
                    pending_input TEXT,
                    pending_approval TEXT,
                    approved_actions TEXT NOT NULL,
                    last_result TEXT,
                    last_error TEXT,
                    command_history TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS command_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    command_arguments TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    UNIQUE(task_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL,
                    command_type TEXT,
                    output TEXT,
                    UNIQUE(task_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS runtime_context (
                    task_id TEXT PRIMARY KEY,
                    context TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    attachment_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    media_type TEXT,
                    metadata TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            self._db.commit()

    def _load_state(self) -> None:
        with self._db_lock, self._lock:
            for row in self._db.execute("SELECT * FROM tasks ORDER BY created_at"):
                self._tasks[row["task_id"]] = TaskRecord(
                    task_id=row["task_id"],
                    user_request=row["user_request"],
                    status=TaskStatus(row["status"]),
                    current_run_id=row["current_run_id"],
                    pending_input=row["pending_input"],
                    pending_approval=_decode(row["pending_approval"]),
                    approved_actions=set(_decode(row["approved_actions"], [])),
                    last_result=_decode(row["last_result"]),
                    last_error=row["last_error"],
                    command_history=list(_decode(row["command_history"], [])),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            for row in self._db.execute("SELECT * FROM command_runs ORDER BY started_at"):
                self._runs[row["run_id"]] = CommandRun(
                    run_id=row["run_id"],
                    task_id=row["task_id"],
                    command=ControllerCommand(
                        CommandType(row["command_type"]),
                        _decode(row["command_arguments"], {}),
                    ),
                    status=RunStatus(row["status"]),
                    result=_decode(row["result"]),
                    error=row["error"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                )

    def create_task(self, user_request: str) -> TaskRecord:
        task = super().create_task(user_request)
        self._persist_task(task)
        return task

    def create_run(self, task_id: str, command: ControllerCommand) -> CommandRun:
        run = super().create_run(task_id, command)
        self._persist_task(self.get_task(task_id))
        self._persist_run(run)
        return run

    def update_task(self, task_id: str, **changes: Any) -> TaskRecord:
        task = super().update_task(task_id, **changes)
        self._persist_task(task)
        return task

    def finish_run(self, run_id: str, status, *, result: Any = None, error: str | None = None) -> CommandRun:
        run = super().finish_run(run_id, status, result=result, error=error)
        self._persist_run(run)
        return run

    def append_message(self, task_id: str, message: Mapping[str, Any]) -> None:
        self._ensure_task(task_id)
        with self._db_lock:
            sequence = self._next_sequence("conversation_messages", task_id)
            self._db.execute(
                "INSERT INTO conversation_messages(task_id, sequence, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
                (task_id, sequence, str(message.get("role", "user")), str(message.get("content", "")), _encode(message)),
            )
            self._db.commit()

    def list_messages(self, task_id: str) -> tuple[dict[str, Any], ...]:
        self._ensure_task(task_id)
        with self._db_lock:
            rows = self._db.execute(
                "SELECT metadata FROM conversation_messages WHERE task_id=? ORDER BY sequence", (task_id,)
            ).fetchall()
        return tuple(_decode(row["metadata"], {}) for row in rows)

    def append_event(self, event: Any) -> None:
        self._ensure_task(event.task_id)
        with self._db_lock:
            sequence = self._next_sequence("conversation_events", event.task_id)
            self._db.execute(
                "INSERT INTO conversation_events(task_id, sequence, kind, message, status, command_type, output) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.task_id,
                    sequence,
                    event.kind,
                    event.message,
                    event.status.value,
                    event.command_type,
                    _encode(event.output),
                ),
            )
            self._db.commit()

    def list_events(self, task_id: str) -> tuple[dict[str, Any], ...]:
        self._ensure_task(task_id)
        with self._db_lock:
            rows = self._db.execute(
                "SELECT kind, message, status, command_type, output FROM conversation_events WHERE task_id=? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return tuple(
            {
                "kind": row["kind"],
                "message": row["message"],
                "status": row["status"],
                "command_type": row["command_type"],
                "output": _decode(row["output"]),
            }
            for row in rows
        )

    def save_context(self, task_id: str, context: PlatformContext) -> None:
        self._ensure_task(task_id)
        payload = {
            "workspace_root": context.workspace_root,
            "available_tools": list(context.available_tools),
            "connected_integrations": list(context.connected_integrations),
            "attachments": list(context.attachments),
            "task_status": context.task_status,
            "approval_required_actions": list(context.approval_required_actions),
        }
        with self._db_lock:
            self._db.execute(
                "INSERT INTO runtime_context(task_id, context) VALUES (?, ?) ON CONFLICT(task_id) DO UPDATE SET context=excluded.context",
                (task_id, _encode(payload)),
            )
            self._db.commit()

    def load_context(self, task_id: str) -> PlatformContext | None:
        self._ensure_task(task_id)
        with self._db_lock:
            row = self._db.execute("SELECT context FROM runtime_context WHERE task_id=?", (task_id,)).fetchone()
        data = _decode(row["context"], {}) if row else None
        if not data:
            return None
        return PlatformContext(
            workspace_root=data.get("workspace_root", "./workspace"),
            available_tools=tuple(data.get("available_tools", [])),
            connected_integrations=tuple(data.get("connected_integrations", [])),
            attachments=tuple(data.get("attachments", [])),
            task_status=data.get("task_status", "new"),
            approval_required_actions=tuple(data.get("approval_required_actions", [])),
        )

    def record_activity(self, task_id: str, activity_type: str, payload: Mapping[str, Any]) -> None:
        self._ensure_task(task_id)
        with self._db_lock:
            self._db.execute(
                "INSERT INTO activity(task_id, activity_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (task_id, activity_type, _encode(payload), time.time()),
            )
            self._db.commit()

    def list_activity(self, task_id: str, activity_type: str | None = None) -> tuple[dict[str, Any], ...]:
        self._ensure_task(task_id)
        query = "SELECT activity_type, payload, created_at FROM activity WHERE task_id=?"
        params: tuple[Any, ...] = (task_id,)
        if activity_type:
            query += " AND activity_type=?"
            params += (activity_type,)
        query += " ORDER BY id"
        with self._db_lock:
            rows = self._db.execute(query, params).fetchall()
        return tuple(
            {"type": row["activity_type"], "payload": _decode(row["payload"], {}), "created_at": row["created_at"]}
            for row in rows
        )

    def record_attachment(self, task_id: str, attachment_id: str, path: str, media_type: str | None = None, metadata: Mapping[str, Any] | None = None) -> None:
        self._ensure_task(task_id)
        with self._db_lock:
            self._db.execute(
                "INSERT OR REPLACE INTO attachments(attachment_id, task_id, path, media_type, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (attachment_id, task_id, path, media_type, _encode(metadata or {}), time.time()),
            )
            self._db.commit()

    def list_attachments(self, task_id: str) -> tuple[dict[str, Any], ...]:
        self._ensure_task(task_id)
        with self._db_lock:
            rows = self._db.execute("SELECT * FROM attachments WHERE task_id=? ORDER BY created_at", (task_id,)).fetchall()
        return tuple({"attachment_id": row["attachment_id"], "path": row["path"], "media_type": row["media_type"], "metadata": _decode(row["metadata"], {})} for row in rows)

    def record_artifact(self, task_id: str, artifact_id: str, path: str, artifact_type: str, metadata: Mapping[str, Any] | None = None) -> None:
        self._ensure_task(task_id)
        with self._db_lock:
            self._db.execute(
                "INSERT OR REPLACE INTO artifacts(artifact_id, task_id, path, artifact_type, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact_id, task_id, path, artifact_type, _encode(metadata or {}), time.time()),
            )
            self._db.commit()

    def list_artifacts(self, task_id: str) -> tuple[dict[str, Any], ...]:
        self._ensure_task(task_id)
        with self._db_lock:
            rows = self._db.execute("SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at", (task_id,)).fetchall()
        return tuple({"artifact_id": row["artifact_id"], "path": row["path"], "artifact_type": row["artifact_type"], "metadata": _decode(row["metadata"], {})} for row in rows)

    def close(self) -> None:
        with self._db_lock:
            self._db.close()

    def _persist_task(self, task: TaskRecord) -> None:
        with self._db_lock:
            self._db.execute(
                """INSERT INTO tasks(task_id, user_request, status, current_run_id, pending_input, pending_approval,
                   approved_actions, last_result, last_error, command_history, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET user_request=excluded.user_request, status=excluded.status,
                   current_run_id=excluded.current_run_id, pending_input=excluded.pending_input,
                   pending_approval=excluded.pending_approval, approved_actions=excluded.approved_actions,
                   last_result=excluded.last_result, last_error=excluded.last_error,
                   command_history=excluded.command_history, updated_at=excluded.updated_at""",
                (
                    task.task_id,
                    task.user_request,
                    task.status.value,
                    task.current_run_id,
                    task.pending_input,
                    _encode(task.pending_approval),
                    _encode(sorted(task.approved_actions)),
                    _encode(task.last_result),
                    task.last_error,
                    _encode(task.command_history),
                    task.created_at,
                    task.updated_at,
                ),
            )
            self._db.commit()

    def _persist_run(self, run: CommandRun) -> None:
        with self._db_lock:
            self._db.execute(
                """INSERT INTO command_runs(run_id, task_id, command_type, command_arguments, status, result, error, started_at, finished_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, result=excluded.result,
                   error=excluded.error, finished_at=excluded.finished_at""",
                (
                    run.run_id,
                    run.task_id,
                    run.command.type.value,
                    _encode(run.command.arguments),
                    run.status.value,
                    _encode(run.result),
                    run.error,
                    run.started_at,
                    run.finished_at,
                ),
            )
            self._db.commit()

    def _next_sequence(self, table: str, task_id: str) -> int:
        row = self._db.execute(f"SELECT COALESCE(MAX(sequence), -1) + 1 AS next FROM {table} WHERE task_id=?", (task_id,)).fetchone()
        return int(row["next"])

    def _ensure_task(self, task_id: str) -> None:
        self.get_task(task_id)

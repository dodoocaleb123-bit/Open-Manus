"""Durable local scheduling owned by the application, not by a model."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping


class ScheduleError(ValueError):
    """Raised when a schedule is invalid."""


class LocalScheduler:
    """Persist schedule definitions and dispatch due instructions to DeepSeek."""

    def __init__(self, database_path: str | Path = "./workspace/schedules.sqlite3") -> None:
        self.database_path = str(database_path)
        path = Path(self.database_path)
        if self.database_path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.database_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.execute("""CREATE TABLE IF NOT EXISTS schedules (
            schedule_id TEXT PRIMARY KEY, name TEXT NOT NULL, instruction TEXT NOT NULL,
            schedule_kind TEXT NOT NULL, interval_seconds INTEGER, fire_at REAL,
            enabled INTEGER NOT NULL, next_run REAL, last_run REAL, last_task_id TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        )""")
        self._db.commit()

    def create(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        name = str(arguments.get("name", "")).strip()
        instruction = str(arguments.get("instruction", "")).strip()
        if not name or not instruction:
            raise ScheduleError("name and instruction are required")
        kind = str(arguments.get("schedule_kind", "interval"))
        now = time.time()
        interval = arguments.get("interval_seconds")
        fire_at = arguments.get("fire_at")
        if kind == "interval":
            try:
                interval = int(interval)
            except (TypeError, ValueError) as exc:
                raise ScheduleError("interval_seconds is required") from exc
            if interval < 60:
                raise ScheduleError("interval_seconds must be at least 60")
            next_run = now + interval
        elif kind == "once":
            try:
                fire_at = float(fire_at)
            except (TypeError, ValueError) as exc:
                raise ScheduleError("fire_at is required for once schedules") from exc
            if fire_at <= now:
                raise ScheduleError("fire_at must be in the future")
            next_run = fire_at
            interval = None
        else:
            raise ScheduleError("schedule_kind must be interval or once")
        schedule_id = f"schedule_{uuid.uuid4().hex}"
        with self._lock:
            self._db.execute("INSERT INTO schedules VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL, ?, ?)", (schedule_id, name, instruction, kind, interval, fire_at, next_run, now, now))
            self._db.commit()
        return self.get(schedule_id)

    def list(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM schedules"
        params: tuple[Any, ...] = ()
        if enabled is not None:
            query += " WHERE enabled=?"
            params = (int(enabled),)
        query += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [self._row(row) for row in rows]

    def get(self, schedule_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
        if row is None:
            raise ScheduleError("schedule does not exist")
        return self._row(row)

    def set_enabled(self, schedule_id: str, enabled: bool) -> dict[str, Any]:
        self.get(schedule_id)
        with self._lock:
            self._db.execute("UPDATE schedules SET enabled=?, updated_at=? WHERE schedule_id=?", (int(enabled), time.time(), schedule_id))
            self._db.commit()
        return self.get(schedule_id)

    def due(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        with self._lock:
            rows = self._db.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=? ORDER BY next_run", (now,)).fetchall()
        return [self._row(row) for row in rows]

    def run_due(self, dispatch: Callable[[str, Mapping[str, Any]], Mapping[str, Any]], *, now: float | None = None) -> list[dict[str, Any]]:
        results = []
        for item in self.due(now):
            try:
                result = dict(dispatch(item["instruction"], {"schedule_id": item["schedule_id"], "name": item["name"], "scheduled_at": item["next_run"]}))
                task_id = result.get("task_id")
                status = "dispatched"
            except Exception as exc:
                result = {"error": str(exc)}
                task_id = None
                status = "failed"
            current = time.time()
            if item["schedule_kind"] == "interval":
                next_run = current + int(item["interval_seconds"])
                enabled = 1
            else:
                next_run = None
                enabled = 0
            with self._lock:
                self._db.execute("UPDATE schedules SET enabled=?, next_run=?, last_run=?, last_task_id=?, updated_at=? WHERE schedule_id=?", (enabled, next_run, current, task_id, current, item["schedule_id"]))
                self._db.commit()
            results.append({"schedule_id": item["schedule_id"], "status": status, "task_id": task_id, "result": result, "next_run": next_run})
        return results

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        return data

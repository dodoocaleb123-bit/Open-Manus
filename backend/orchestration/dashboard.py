"""Read-only collaboration dashboard data derived from the DeepSeek loop."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentActivity:
    agent_id: str
    model: str
    role: str
    status: str
    activities: int
    last_objective: str = ""
    last_result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OrchestrationDashboard:
    """Summarize collaboration without taking over DeepSeek's decisions."""

    AGENTS = {
        "deepseek": ("deepseek", "controller", "Primary controller"),
        "research": ("qwen2.5:3b", "research", "Research specialist"),
        "coder": ("qwen2.5-coder:7b", "coder", "Coding specialist"),
        "vision": ("gemma3:4b", "vision", "Visual specialist"),
        "creative": ("llama3.2:3b", "creative", "Creative specialist"),
    }

    def __init__(self, store: Any) -> None:
        self.store = store

    def task_snapshot(self, task_id: str) -> dict[str, Any]:
        task = self.store.snapshot(task_id)
        activity = list(self.store.list_activity(task_id)) if hasattr(self.store, "list_activity") else []
        agents = self._agents(activity)
        delegation_edges = []
        for item in activity:
            if item["type"] == "delegated_call":
                payload = item.get("payload", {})
                role = str(payload.get("role", "specialist"))
                delegation_edges.append({"from": "deepseek", "to": role, "objective": payload.get("objective", ""), "run_id": payload.get("run_id")})
        return {
            "task_id": task_id,
            "status": task.status.value,
            "user_request": task.user_request,
            "controller": {"id": "deepseek", "model": "deepseek", "status": task.status.value},
            "agents": [agent.to_dict() for agent in agents],
            "delegation_edges": delegation_edges,
            "activity_count": len(activity),
            "approval": dict(task.pending_approval) if task.pending_approval else None,
            "collaboration_rule": "DeepSeek decides; specialists return results; the execution layer enforces tools and approvals.",
        }

    def overview(self) -> dict[str, Any]:
        tasks = self.store.list_tasks()
        snapshots = [self.task_snapshot(task.task_id) for task in tasks[:50]]
        counts = Counter(task["status"] for task in snapshots)
        return {"controller": "deepseek", "tasks": snapshots, "task_counts": dict(counts), "agent_registry": [{"id": key, "model": value[0], "role": value[1], "purpose": value[2]} for key, value in self.AGENTS.items()]}

    def _agents(self, activity: list[Mapping[str, Any]]) -> list[AgentActivity]:
        grouped: dict[str, list[Mapping[str, Any]]] = {key: [] for key in self.AGENTS}
        for item in activity:
            payload = item.get("payload", {})
            role = str(payload.get("role", ""))
            if item["type"] == "controller_decision":
                grouped["deepseek"].append(item)
            elif role in grouped:
                grouped[role].append(item)
        result = []
        for agent_id, (model, role, _) in self.AGENTS.items():
            items = grouped[agent_id]
            last_objective = ""
            last_result = ""
            for item in reversed(items):
                payload = item.get("payload", {})
                if not last_objective and payload.get("objective"):
                    last_objective = str(payload["objective"])
                if not last_result and (payload.get("content") or payload.get("result")):
                    last_result = str(payload.get("content", payload.get("result", "")))
                if last_objective and last_result:
                    break
            result.append(AgentActivity(agent_id, model, role, "active" if items else "idle", len(items), last_objective[:500], last_result[:500]))
        return result

"""Dependency-light local HTTP API for the Open-Manus GUI."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import threading
from dataclasses import asdict, is_dataclass
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from conversation import DeepSeekConversationLoop
from attachments import AttachmentPipeline
from execution import ControlledExecutor, SQLiteStateStore, TaskStatus, ToolSpec
from model_adapters import DeepSeekController, ModelRegistry, ModelRole, PlatformContext

FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _task_payload(task: Any) -> dict[str, Any]:
    return _jsonable(asdict(task))


def _result_payload(result: Any) -> dict[str, Any]:
    return _jsonable(asdict(result))


def _context_payload(payload: Mapping[str, Any] | None) -> PlatformContext:
    payload = payload or {}
    return PlatformContext(
        workspace_root=str(payload.get("workspace_root", "./workspace")),
        available_tools=tuple(payload.get("available_tools", ())),
        connected_integrations=tuple(payload.get("connected_integrations", ())),
        attachments=tuple(payload.get("attachments", ())),
        task_status=str(payload.get("task_status", "new")),
        approval_required_actions=tuple(payload.get("approval_required_actions", ())),
    )


class OpenManusAPI:
    """Application service used by both HTTP handlers and direct tests."""

    def __init__(
        self,
        *,
        controller: DeepSeekController | None = None,
        executor: ControlledExecutor | None = None,
        registry: ModelRegistry | None = None,
        store: SQLiteStateStore | None = None,
        max_turns: int = 20,
        tools: tuple[ToolSpec, ...] = (),
    ) -> None:
        self.store = store or (executor.store if executor is not None else SQLiteStateStore.from_environment())
        self.attachments = AttachmentPipeline(self.store)
        registry = registry or (executor.registry if executor is not None else self._default_registry())
        self.executor = executor or ControlledExecutor(registry, store=self.store, tools=tools)
        self.controller = controller or DeepSeekController(registry.get(ModelRole.CONTROLLER))
        self.loop = DeepSeekConversationLoop(self.controller, self.executor, max_turns=max_turns)
        self._task_lock = threading.Lock()

    @staticmethod
    def _default_registry() -> ModelRegistry:
        return ModelRegistry.from_environment()

    async def health(self) -> dict[str, Any]:
        health = {
            role.value: await self.executor.registry.get(role).health_check()
            for role in self.executor.registry.roles()
        }
        return {
            "status": "ok",
            "storage": str(getattr(self.store, "database_path", "memory")),
            "models": _jsonable(health),
        }

    def list_tasks(self) -> dict[str, Any]:
        return {"tasks": [_task_payload(task) for task in self.store.list_tasks()]}

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self.store.snapshot(task_id)
        payload = {"task": _task_payload(task)}
        if hasattr(self.store, "list_messages"):
            payload["messages"] = _jsonable(self.store.list_messages(task_id))
            payload["events"] = _jsonable(self.store.list_events(task_id))
            payload["activity"] = _jsonable(self.store.list_activity(task_id))
            payload["attachments"] = _jsonable(self.store.list_attachments(task_id))
            payload["artifacts"] = _jsonable(self.store.list_artifacts(task_id))
            payload["context"] = _jsonable(self.store.load_context(task_id))
        else:
            payload["messages"] = []
            payload["events"] = []
            payload["activity"] = []
            payload["attachments"] = []
            payload["artifacts"] = []
        return payload

    async def create_task(self, body: Mapping[str, Any]) -> dict[str, Any]:
        message = str(body.get("message", "")).strip()
        if not message:
            raise ValueError("message must not be empty")
        context = _context_payload(body.get("context"))
        with self._task_lock:
            result = await self.loop.start(message, context=context, tools=body.get("tools", ()))
        return _result_payload(result)

    async def provide_input(self, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        value = str(body.get("value", ""))
        with self._task_lock:
            result = await self.loop.provide_user_input(task_id, value, tools=body.get("tools", ()))
        return _result_payload(result)

    async def approve(self, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        action = str(body.get("action", ""))
        with self._task_lock:
            result = await self.loop.approve(task_id, action, tools=body.get("tools", ()))
        return _result_payload(result)

    async def cancel(self, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        reason = str(body.get("reason", "Cancelled by user"))
        with self._task_lock:
            result = await self.loop.cancel(task_id, reason)
        return _result_payload(result)

    async def retry(self, task_id: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._task_lock:
            result = await self.loop.retry(task_id, tools=(body or {}).get("tools", ()))
        return _result_payload(result)

    def catalog(self) -> dict[str, Any]:
        workspace = Path(os.getenv("OPENMANUS_WORKSPACE_ROOT", "./workspace")).resolve()
        projects = []
        if workspace.is_dir():
            projects = [{"name": path.name, "path": str(path), "kind": "folder"} for path in sorted(workspace.iterdir()) if path.is_dir()]
        return {
            "agents": [
                {"id": "deepseek", "name": "DeepSeek", "role": "Controller", "primary": True, "model": os.getenv("DEEPSEEK_CONTROLLER_MODEL", "deepseek-r1:7b")},
                {"id": "research", "name": "Qwen Research", "role": "Research specialist", "model": os.getenv("RESEARCH_LLM_MODEL", "qwen2.5:3b")},
                {"id": "coder", "name": "Qwen Coder", "role": "Coding specialist", "model": os.getenv("CODER_LLM_MODEL", "qwen2.5-coder:7b")},
                {"id": "vision", "name": "Gemma Vision", "role": "Visual specialist", "model": os.getenv("VISION_LLM_MODEL", "gemma3:4b")},
                {"id": "creative", "name": "Llama Creative", "role": "Creative specialist", "model": os.getenv("CREATIVE_LLM_MODEL", "llama3.2:3b")},
            ],
            "skills": [
                {"id": "research", "name": "Web research", "status": "ready"},
                {"id": "coding", "name": "Software engineering", "status": "ready"},
                {"id": "vision", "name": "Image and document analysis", "status": "ready"},
                {"id": "creative", "name": "Creative direction", "status": "ready"},
            ],
            "plugins": [{"id": "github", "name": "GitHub", "status": "connected" if os.getenv("GITHUB_ENABLED", "false").lower() == "true" else "disabled"}],
            "scheduled_tasks": [],
            "projects": projects,
            "library": [{"task_id": task.task_id, "request": task.user_request, "status": task.status.value} for task in self.store.list_tasks()],
            "services": {
                "ollama": {"name": "Ollama", "base_url": os.getenv("DEEPSEEK_CONTROLLER_BASE_URL", "http://localhost:11434/v1"), "status": "local"},
                "github": {"name": "GitHub", "status": "connected" if os.getenv("GITHUB_ENABLED", "false").lower() == "true" else "disabled"},
            },
        }

    def add_attachment(self, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        path = str(body.get("path", "")).strip()
        if not path:
            raise ValueError("path is required")
        record = self.attachments.ingest_path(task_id, path)
        self.loop.add_attachment_context(task_id, record.to_dict())
        return {"attachment": record.to_dict()}

    def add_uploaded_attachment(self, task_id: str, filename: str, content_type: str | None, data: bytes) -> dict[str, Any]:
        if len(data) > 250 * 1024 * 1024:
            raise ValueError("uploaded file is larger than 250 MB")
        record = self.attachments.ingest(task_id, filename, content_type, data)
        self.loop.add_attachment_context(task_id, record.to_dict())
        return {"attachment": record.to_dict()}

    def add_artifact(self, task_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        artifact_id = str(body.get("artifact_id", "")).strip()
        path = str(body.get("path", "")).strip()
        artifact_type = str(body.get("artifact_type", "file")).strip()
        if not artifact_id or not path:
            raise ValueError("artifact_id and path are required")
        self.store.record_artifact(task_id, artifact_id, path, artifact_type, body.get("metadata", {}))
        return {"artifact": self.store.list_artifacts(task_id)[-1]}


class _RequestHandler(BaseHTTPRequestHandler):
    api: OpenManusAPI
    frontend_root: Path = FRONTEND_ROOT

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _read_multipart_file(self) -> tuple[str, str | None, bytes]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 251 * 1024 * 1024:
            raise ValueError("multipart request is larger than 251 MB")
        raw = self.rfile.read(length)
        headers = f"Content-Type: {self.headers.get('Content-Type', '')}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        message = BytesParser(policy=policy.default).parsebytes(headers + raw)
        for part in message.iter_attachments():
            payload = part.get_payload(decode=True) or b""
            return part.get_filename() or "upload.bin", part.get_content_type(), payload
        raise ValueError("multipart request did not contain a file")

    def _route(self) -> tuple[str, list[str], dict[str, list[str]]]:
        parsed = urlparse(self.path)
        return parsed.path, [unquote(part) for part in parsed.path.split("/") if part], parse_qs(parsed.query)

    def do_GET(self) -> None:
        path, parts, _ = self._route()
        try:
            if path == "/api/health":
                self._send_json(asyncio.run(self.api.health()))
                return
            if path == "/api/tasks":
                self._send_json(self.api.list_tasks())
                return
            if len(parts) == 3 and parts[:2] == ["api", "tasks"]:
                self._send_json(self.api.get_task(parts[2]))
                return
            if path == "/api/catalog":
                self._send_json(self.api.catalog())
                return
            self._serve_static(path)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND if "Unknown task" in str(exc) else HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        path, parts, _ = self._route()
        try:
            if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "attachments" and self.headers.get("Content-Type", "").startswith("multipart/"):
                filename, content_type, data = self._read_multipart_file()
                self._send_json(self.api.add_uploaded_attachment(parts[2], filename, content_type, data), HTTPStatus.CREATED)
                return
            body = self._read_json()
            if path == "/api/tasks":
                self._send_json(asyncio.run(self.api.create_task(body)), HTTPStatus.CREATED)
                return
            if len(parts) == 4 and parts[:2] == ["api", "tasks"]:
                task_id, action = parts[2], parts[3]
                if action == "input":
                    self._send_json(asyncio.run(self.api.provide_input(task_id, body)))
                    return
                if action == "approve":
                    self._send_json(asyncio.run(self.api.approve(task_id, body)))
                    return
                if action == "cancel":
                    self._send_json(asyncio.run(self.api.cancel(task_id, body)))
                    return
                if action == "retry":
                    self._send_json(asyncio.run(self.api.retry(task_id, body)))
                    return
                if action == "attachments":
                    self._send_json(self.api.add_attachment(task_id, body), HTTPStatus.CREATED)
                    return
                if action == "artifacts":
                    self._send_json(self.api.add_artifact(task_id, body), HTTPStatus.CREATED)
                    return
            self._send_json({"error": "Route not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"/", ""} else path.lstrip("/")
        candidate = (self.frontend_root / relative).resolve()
        if self.frontend_root not in candidate.parents and candidate != self.frontend_root:
            raise ValueError("invalid static path")
        if not candidate.is_file():
            candidate = self.frontend_root / "index.html"
        data = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str = "127.0.0.1", port: int = 8000, api: OpenManusAPI | None = None) -> ThreadingHTTPServer:
    app = api or OpenManusAPI()

    class Handler(_RequestHandler):
        pass

    Handler.api = app
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = create_server(host, port)
    print(f"Open-Manus local GUI: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if hasattr(server.RequestHandlerClass.api.store, "close"):
            server.RequestHandlerClass.api.store.close()


if __name__ == "__main__":
    serve(os.getenv("OPENMANUS_HOST", "127.0.0.1"), int(os.getenv("OPENMANUS_PORT", "8000")))

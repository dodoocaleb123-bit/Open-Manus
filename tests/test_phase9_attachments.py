import asyncio
import json

import pytest

from api import OpenManusAPI
from attachments import AttachmentError, AttachmentPipeline
from execution import ControlledExecutor, SQLiteStateStore
from model_adapters import (
    CommandType,
    ControllerCommand,
    HealthStatus,
    ModelResponse,
    ModelRole,
)

PNG_1X1 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


class Adapter:
    def __init__(self, role, content="vision findings"):
        self.role = role
        self.model = f"{role.value}-test"
        self.content = content

    async def generate(self, request):
        return ModelResponse(role=self.role, model=self.model, content=self.content)

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class Registry:
    def __init__(self):
        self.adapters = {role: Adapter(role) for role in ModelRole}
        self.adapters[ModelRole.VISION] = Adapter(ModelRole.VISION, '{"equation":"x=1","objects":["triangle"]}')

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def run(coro):
    return asyncio.run(coro)


def test_image_pipeline_validates_stores_previews_and_describes_gemma_support(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "attachments.sqlite3")
    task = store.create_task("Analyze this image")
    pipeline = AttachmentPipeline(store)
    record = pipeline.ingest(task.task_id, "math.png", "image/png", PNG_1X1)
    assert record.scan_status == "passed_local_safety_scan"
    assert record.preview_status.startswith("image_preview_available_1x1")
    assert record.gemma_supported is True
    assert "Gemma" in record.description
    assert record.sha256
    assert record.path.endswith(".png")
    store.close()


def test_pipeline_rejects_executables_and_mismatched_images(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "reject.sqlite3")
    task = store.create_task("Reject unsafe files")
    pipeline = AttachmentPipeline(store)
    with pytest.raises(AttachmentError, match="executable"):
        pipeline.ingest(task.task_id, "run.exe", "application/octet-stream", b"MZ executable")
    with pytest.raises(AttachmentError, match="PNG"):
        pipeline.ingest(task.task_id, "fake.png", "image/png", b"not an image")
    store.close()


def test_api_upload_adds_safe_description_to_deepseek_context(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "context.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    task = executor.create_task("Solve the image")
    result = api.add_uploaded_attachment(task.task_id, "equation.png", "image/png", PNG_1X1)
    messages = store.list_messages(task.task_id)
    assert result["attachment"]["gemma_supported"] is True
    assert "gemma_supported" in messages[0]["content"]
    assert "equation.png" in store.load_context(task.task_id).attachments
    store.close()


def test_vision_delegation_returns_structured_gemma_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "gemma.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    task = executor.create_task("Inspect the equation")
    result = run(executor.execute(task.task_id, ControllerCommand(CommandType.DELEGATE_TO_MODEL, {"role": "vision", "objective": "Read equation", "expected_output": "structured findings"})))
    payload = json.loads(result.output)
    assert payload["specialist"] == "gemma"
    assert payload["analysis_type"] == "visual_attachment"
    assert payload["findings"]["equation"] == "x=1"
    store.close()

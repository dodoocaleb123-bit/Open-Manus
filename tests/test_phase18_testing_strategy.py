"""Phase 18: deterministic role and end-to-end acceptance tests.

These tests deliberately use local fixtures and fake adapters. They validate role
boundaries, command protocol, persistence, approvals, and recovery without
requiring Ollama, browser credentials, or external network access.
"""
import asyncio
import json
import subprocess

import pytest

from attachments import AttachmentPipeline
from coding import CodingWorkspace
from conversation import DeepSeekConversationLoop
from creative import CreativeWorkflows
from execution import ControlledExecutor, SQLiteStateStore, TaskStatus, ToolSpec
from model_adapters import (
    CommandType,
    ControllerCommand,
    DeepSeekController,
    HealthStatus,
    ModelConfigurationError,
    ModelResponse,
    ModelRole,
)
from research import ResearchTools
from security import SecretStore, SecureActions

PNG_1X1 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def run(coro):
    return asyncio.run(coro)


def decision(message, status="working", command=None):
    return json.dumps({"assistant_message": message, "status": status, "command": command})


def command(command_type, arguments):
    return {"type": command_type, "arguments": arguments}


class Adapter:
    def __init__(self, role, content="specialist result", failure=None):
        self.role = role
        self.model = f"{role.value}-test"
        self.content = content
        self.failure = failure
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure
        return ModelResponse(role=self.role, model=self.model, content=self.content)

    async def health_check(self):
        return HealthStatus(self.role, self.model, True, 200, "ok")


class Registry:
    def __init__(self, content="specialist result"):
        self.adapters = {role: Adapter(role, content) for role in ModelRole}

    def get(self, role):
        if role not in self.adapters:
            raise ModelConfigurationError(f"No adapter configured for role {role.value}")
        return self.adapters[role]


def make_loop(decisions, *, tools=(), registry=None, max_turns=10):
    controller_adapter = Adapter(ModelRole.CONTROLLER, content="controller fixture")
    controller_adapter.decisions = list(decisions)

    async def generate(request):
        controller_adapter.requests.append(request)
        if not controller_adapter.decisions:
            raise AssertionError("DeepSeek was called more times than expected")
        return ModelResponse(ModelRole.CONTROLLER, controller_adapter.model, controller_adapter.decisions.pop(0))

    controller_adapter.generate = generate
    controller = DeepSeekController(controller_adapter)
    store = SQLiteStateStore(":memory:")
    executor = ControlledExecutor(registry or Registry(), store=store, tools=tools)
    return DeepSeekConversationLoop(controller, executor, max_turns=max_turns), store, controller_adapter


# DeepSeek role tests

def test_deepseek_understands_delegates_and_synthesizes():
    loop, store, adapter = make_loop([
        decision("I understand the request and will research it.", command=command("delegate_to_model", {"role": "research", "objective": "Find evidence", "expected_output": "cited findings"})),
        decision("The evidence is ready; here is the synthesized answer.", status="completed", command=command("complete_task", {})),
    ])
    result = run(loop.start("Compare two products"))
    assert result.status is TaskStatus.COMPLETED
    assert len(adapter.requests) == 2
    assert adapter.requests[0].messages[0]["role"] == "system"
    assert "DeepSeek" in adapter.requests[0].messages[0]["content"]
    assert any("specialist result" in str(message) for message in result.messages)
    store.close()


def test_deepseek_approval_failure_and_recovery():
    calls = []
    state = {"fail": True}

    def risky(_args):
        calls.append("called")
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("temporary failure")
        return "recovered"

    loop, store, _ = make_loop([
        decision("I will perform the operation.", command=command("run_tool", {"tool": "risky", "arguments": {}})),
        decision("The operation recovered.", status="completed", command=command("complete_task", {})),
    ], tools=(ToolSpec("risky", risky, retry_safe=True),))
    failed = run(loop.start("Do the risky operation"))
    assert failed.status is TaskStatus.FAILED
    recovered = run(loop.retry(failed.task_id))
    assert recovered.status is TaskStatus.COMPLETED
    assert calls == ["called", "called"]
    store.close()


# Qwen research role tests

def test_qwen_search_browser_navigation_and_page_extraction():
    class FakeResearch(ResearchTools):
        def search(self, arguments):
            return {"query": arguments["query"], "results": [{"url": "https://example.test/page", "title": "Page", "snippet": "snippet"}]}

        def extract_page(self, arguments):
            return {"url": arguments["url"], "title": "Page", "text": "Extracted source text", "links": ["https://example.test/next"], "script_count": 1, "dynamic_hint": True}

    tools = FakeResearch(dynamic_inspector=lambda url: {"url": url, "dynamic": True, "rendering": "playwright", "text": "Rendered browser text"})
    assert tools.search({"query": "test"})["results"][0]["url"].startswith("https://")
    page = tools.extract_page({"url": "https://example.test/page"})
    assert page["text"] == "Extracted source text" and page["links"]
    dynamic = tools.inspect_dynamic({"url": "https://example.test/page"})
    assert dynamic["rendering"] == "playwright"


def test_qwen_citations_and_multi_source_comparison():
    class FakeResearch(ResearchTools):
        def search(self, arguments):
            return {"query": arguments["query"], "results": [{"url": f"https://example.test/{arguments['query']}", "title": arguments["query"], "snippet": "evidence"}]}

        def extract_page(self, arguments):
            return {"url": arguments["url"], "title": arguments["url"].rsplit("/", 1)[-1], "text": "shared evidence", "dynamic_hint": False}

    result = FakeResearch().collect_sources({"objective": "Compare sources", "queries": ["one", "two"], "max_sources": 2})
    assert len(result["sources"]) == 2
    assert len(result["citations"]) == 2
    assert "shared evidence" in result["evidence_summary"]


# Qwen Coder role tests

def test_qwen_coder_project_edit_test_preview_and_error_repair(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    assert workspace.create_project({"project": "app"})["created"]
    workspace.write_file({"project": "app", "path": "test_app.py", "content": "def test_ok():\n    assert 2 + 2 == 4\n"})
    assert workspace.run_tests({"project": "app", "command": ["pytest", "-q"]})["status"] == "passed"
    bad = workspace.run_command({"project": "app", "command": ["python3", "-c", "raise SystemExit(2)"]})
    assert bad["status"] == "failed"
    workspace.write_file({"project": "app", "path": "fixed.py", "content": "print('fixed')\n"})
    assert workspace.run_command({"project": "app", "command": ["python3", "fixed.py"]})["status"] == "passed"
    preview = workspace.start_preview({"project": "app", "port": 18766})
    try:
        assert workspace.preview_status({"preview_id": preview["preview_id"]})["status"] == "running"
    finally:
        workspace.stop_preview({"preview_id": preview["preview_id"]})


def test_qwen_coder_github_push_requires_and_records_approval(tmp_path):
    calls = []
    loop, store, _ = make_loop([
        decision("I need approval before pushing.", command=command("run_tool", {"tool": "push", "arguments": {"project": "app"}})),
        decision("The approved push is complete.", status="completed", command=command("complete_task", {})),
    ], tools=(ToolSpec("push", lambda args: calls.append(args) or {"pushed": True}, approval_action="push_to_github"),))
    waiting = run(loop.start("Push the app to GitHub"))
    assert waiting.status is TaskStatus.WAITING_FOR_APPROVAL
    finished = run(loop.approve(waiting.task_id, "push_to_github"))
    assert finished.status is TaskStatus.COMPLETED
    assert calls == [{"project": "app"}]
    assert store.list_approvals(waiting.task_id)[0]["approved"] is True
    store.close()


# Gemma role tests

def test_gemma_image_screenshot_pdf_ocr_and_visual_question_answering(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "vision.sqlite3")
    task = store.create_task("Analyze attachments")
    pipeline = AttachmentPipeline(store)
    image = pipeline.ingest(task.task_id, "screenshot.png", "image/png", PNG_1X1)
    assert image.gemma_supported and "1x1" in image.preview_status
    pdf_result = subprocess.CompletedProcess(["pdftotext"], 0, stdout="OCR text: x squared", stderr="")
    monkeypatch.setattr("attachments.pipeline.subprocess.run", lambda *args, **kwargs: pdf_result)
    pdf = pipeline.ingest(task.task_id, "document.pdf", "application/pdf", b"%PDF-1.4 fake")
    assert pdf.gemma_supported and "OCR text" in pdf.extracted_text
    store.close()


def test_gemma_structured_visual_question_answering(tmp_path):
    registry = Registry(content='{"answer":"The triangle is blue","objects":["triangle"],"text":"x=1"}')
    store = SQLiteStateStore(tmp_path / "gemma-e2e.sqlite3")
    executor = ControlledExecutor(registry, store=store)
    task = executor.create_task("Answer the screenshot question")
    result = run(executor.execute(task.task_id, ControllerCommand(CommandType.DELEGATE_TO_MODEL, {"role": "vision", "objective": "What is the triangle color?", "expected_output": "visual answer"})))
    findings = json.loads(result.output)["findings"]
    assert findings["answer"] == "The triangle is blue"
    store.close()


# Llama role tests

def test_llama_creative_concepts_design_direction_presentation_and_image_prompt():
    workflows = CreativeWorkflows()
    brief = workflows.create_brief({"objective": "Launch a youth program", "audience": "students"})
    direction = workflows.visual_direction({"objective": "Launch a youth program", "style": "bold editorial", "palette": ["blue", "yellow"]})
    presentation = workflows.presentation_structure({"objective": "Launch a youth program", "sections": ["Need", "Program", "Impact"]})
    prompt = workflows.image_prompt({"objective": "Youth poster", "style": "bold editorial", "avoid": ["generic stock photo"]})
    assert brief["workflow"] == "creative_brief"
    assert direction["deliverable"]["palette"] == ["blue", "yellow"]
    assert len(presentation["deliverable"]["slides"]) == 3
    assert "generic stock photo" in prompt["deliverable"]["prompt"]


# End-to-end acceptance matrix

def test_e2e_conversation_and_user_communication():
    loop, store, _ = make_loop([decision("Hello — how can I help?", status="completed", command=command("complete_task", {}))])
    result = run(loop.start("Hello"))
    assert result.status is TaskStatus.COMPLETED and "help" in result.final_message
    store.close()


def test_e2e_image_analysis_and_math_from_uploaded_image(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMANUS_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    store = SQLiteStateStore(tmp_path / "math.sqlite3")
    executor = ControlledExecutor(Registry(content='{"equation":"2+2=4","answer":"4"}'), store=store)
    task = executor.create_task("Solve the uploaded math image")
    attachment = AttachmentPipeline(store).ingest(task.task_id, "math.png", "image/png", PNG_1X1)
    assert attachment.gemma_supported
    result = run(executor.execute(task.task_id, ControllerCommand(CommandType.DELEGATE_TO_MODEL, {"role": "vision", "objective": "Extract and solve the equation", "expected_output": "equation and answer"})))
    assert json.loads(result.output)["findings"]["answer"] == "4"
    store.close()


def test_e2e_internet_research_and_research_to_website_generation(tmp_path):
    class FakeResearch(ResearchTools):
        def search(self, arguments):
            return {"query": arguments["query"], "results": [{"url": "https://example.test/shop", "title": "Shop", "snippet": "layout"}]}

        def extract_page(self, arguments):
            return {"url": arguments["url"], "title": "Shop", "text": "hero catalog checkout", "dynamic_hint": False}

    research = FakeResearch().collect_sources({"objective": "Research cosmetic layouts", "queries": ["cosmetic"], "max_sources": 1})
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "cosmetic"})
    workspace.write_file({"project": "cosmetic", "path": "index.html", "content": "<main>hero catalog checkout</main>"})
    assert research["citations"] and workspace.read_file({"project": "cosmetic", "path": "index.html"})["content"].startswith("<main>")


def test_e2e_visual_preview_analysis(tmp_path):
    workspace = CodingWorkspace(tmp_path / "projects")
    workspace.create_project({"project": "preview"})
    workspace.write_file({"project": "preview", "path": "index.html", "content": "<h1>Preview</h1>"})
    preview = workspace.start_preview({"project": "preview", "port": 18767, "viewport": "mobile"})
    try:
        status = workspace.preview_status({"preview_id": preview["preview_id"]})
        assert status["viewport"] == "mobile" and status["url"].startswith("http://")
    finally:
        workspace.stop_preview({"preview_id": preview["preview_id"]})


def test_e2e_user_cancellation_and_missing_model_handling(tmp_path):
    loop, store, _ = make_loop([decision("I need input.", command=command("request_user_input", {"question": "Continue?"}))])
    waiting = run(loop.start("Start work"))
    cancelled = run(loop.cancel(waiting.task_id, "Stop now"))
    assert cancelled.status is TaskStatus.CANCELLED
    missing = Registry()
    del missing.adapters[ModelRole.RESEARCH]
    executor = ControlledExecutor(missing, store=SQLiteStateStore(tmp_path / "missing.sqlite3"))
    task = executor.create_task("Research")
    result = run(executor.execute(task.task_id, ControllerCommand(CommandType.DELEGATE_TO_MODEL, {"role": "research", "objective": "Search", "expected_output": "report"})))
    assert result.task_status is TaskStatus.FAILED and "No adapter configured" in result.error
    store.close()


def test_e2e_secret_protected_operation(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.sqlite3", "master-key")
    ref = secrets.put("local", "GitHub token", "do-not-log")
    assert ref["value_exposed"] is False and "do-not-log" not in str(secrets.describe("local"))
    workspace = CodingWorkspace(tmp_path / "projects")
    secure = SecureActions(workspace)
    result = secure.use_secret({"secret_ref": ref["secret_ref"]})
    assert result["value_exposed"] is False and result["secret_ref"] == ref["secret_ref"]
    secrets.close()

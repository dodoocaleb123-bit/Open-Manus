import asyncio
import json

from api import OpenManusAPI
from execution import ControlledExecutor, ToolSpec
from model_adapters import CommandType, ControllerCommand, HealthStatus, ModelResponse, ModelRole
from research import ResearchTools
from execution import SQLiteStateStore


class Adapter:
    def __init__(self, role, content='{"summary":"Evidence agrees","confidence":"high"}'):
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

    def get(self, role):
        return self.adapters[role]

    def roles(self):
        return tuple(self.adapters)


def run(coro):
    return asyncio.run(coro)


def test_api_registers_research_tools_without_making_qwen_primary(tmp_path):
    store = SQLiteStateStore(tmp_path / "research.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    api = OpenManusAPI(controller=object(), executor=executor)
    assert {"research_search", "research_extract_page", "research_inspect_dynamic", "research_collect_sources"} <= set(executor._tools)
    assert api.catalog()["agents"][0]["id"] == "deepseek"
    store.close()


def test_source_collection_deduplicates_sources_and_tracks_citations():
    class FakeResearch(ResearchTools):
        def search(self, arguments):
            return {"query": arguments["query"], "results": [{"url": "https://example.com/a", "title": "Example", "snippet": "A"}, {"url": "https://example.com/a", "title": "Duplicate", "snippet": "A"}]}

        def extract_page(self, arguments):
            return {"url": arguments["url"], "title": "Example", "text": "Evidence text", "dynamic_hint": False}

    result = FakeResearch().collect_sources({"objective": "Compare evidence", "queries": ["example"], "max_sources": 5})
    assert len(result["sources"]) == 1
    assert result["sources"][0]["source_id"].startswith("src_")
    assert result["citations"] == [result["sources"][0]["citation"]]
    assert "Evidence text" in result["evidence_summary"]


def test_dynamic_inspection_reports_static_fallback_honestly():
    class FakeResearch(ResearchTools):
        def __init__(self):
            super().__init__(dynamic_inspector=lambda url: None)

        def extract_page(self, arguments):
            return {"url": arguments["url"], "title": "Dynamic", "text": "server HTML", "dynamic_hint": True}

    result = FakeResearch().inspect_dynamic({"url": "https://example.com/app"})
    assert result["rendering"] == "static_fallback"
    assert "not executed" in result["note"]


def test_qwen_research_delegation_returns_structured_findings_and_citations(tmp_path):
    store = SQLiteStateStore(tmp_path / "qwen.sqlite3")
    executor = ControlledExecutor(Registry(), store=store)
    task = executor.create_task("Research the question")
    command = ControllerCommand(CommandType.DELEGATE_TO_MODEL, {"role": "research", "objective": "Compare sources", "inputs": {"sources": [{"url": "https://example.com"}], "citations": ["[src_1] Example"]}, "expected_output": "citation-backed findings"})
    result = run(executor.execute(task.task_id, command))
    payload = json.loads(result.output)
    assert payload["specialist"] == "qwen2.5:3b"
    assert payload["analysis_type"] == "research_evidence_review"
    assert payload["citations"] == ["[src_1] Example"]
    store.close()

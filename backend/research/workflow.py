"""Controlled research workflow tools for Qwen 2.5:3b.

DeepSeek chooses when to call these tools and when to delegate the resulting
source bundle to the research specialist. This module does not make planning
decisions for the controller.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class ResearchSource:
    source_id: str
    url: str
    title: str
    snippet: str = ""
    extracted_text: str = ""
    citation: str = ""
    retrieved_at: str = ""
    dynamic: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchRequest:
    objective: str
    queries: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    max_sources: int = 5
    inspect_dynamic: bool = True

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("research objective must not be empty")
        if self.max_sources < 1 or self.max_sources > 20:
            raise ValueError("max_sources must be between 1 and 20")


@dataclass(frozen=True)
class ResearchResult:
    objective: str
    sources: tuple[ResearchSource, ...]
    evidence_summary: str
    citations: tuple[str, ...]
    search_queries: tuple[str, ...]
    dynamic_pages_inspected: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "sources": [source.to_dict() for source in self.sources],
            "evidence_summary": self.evidence_summary,
            "citations": list(self.citations),
            "search_queries": list(self.search_queries),
            "dynamic_pages_inspected": self.dynamic_pages_inspected,
        }


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = attributes.get("class", "") or ""
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._current = {"url": attributes.get("href", ""), "title": ""}
            self._field = "title"
        elif self._current and tag in {"p", "div"} and "result__snippet" in classes:
            self._field = "snippet"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._field:
            self._current[self._field] = self._current.get(self._field, "") + data

    def handle_endtag(self, tag: str) -> None:
        if self._current and tag == "a" and self._current.get("url"):
            self.results.append({key: " ".join(value.split()) for key, value in self._current.items()})
            self._current = None
            self._field = None


class _PageTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.parts: list[str] = []
        self.links: list[str] = []
        self.scripts = 0
        self._title = False
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "title":
            self._title = True
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        if tag == "script":
            self.scripts += 1
        if tag == "a" and attributes.get("href"):
            self.links.append(urllib.parse.urljoin("", attributes["href"] or ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._title = False
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title += data
        elif not self._skip:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


class ResearchTools:
    """Search and extraction tools exposed to DeepSeek's command protocol."""

    def __init__(
        self,
        *,
        user_agent: str = "Open-Manus-Research/1.0",
        timeout_seconds: float = 20.0,
        dynamic_inspector: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.dynamic_inspector = dynamic_inspector or self._optional_browser_inspector

    def search(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("research_search requires query")
        limit = min(max(int(arguments.get("limit", 5)), 1), 10)
        encoded = urllib.parse.urlencode({"q": query})
        request = urllib.request.Request(
            f"https://html.duckduckgo.com/html/?{encoded}",
            headers={"User-Agent": self.user_agent},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            html = response.read(2_000_000).decode("utf-8", errors="replace")
        parser = _SearchResultParser()
        parser.feed(html)
        results = parser.results[:limit]
        return {"query": query, "results": results, "source_count": len(results)}

    def extract_page(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url", "")).strip()
        if not self._safe_url(url):
            raise ValueError("research_extract_page requires an http(s) URL")
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            body = response.read(5_000_000).decode("utf-8", errors="replace")
        parser = _PageTextParser()
        parser.feed(body)
        return {
            "url": final_url,
            "content_type": content_type,
            "title": " ".join(parser.title.split()),
            "text": " ".join(parser.parts)[:30000],
            "links": parser.links[:100],
            "script_count": parser.scripts,
            "dynamic_hint": parser.scripts > 0,
        }

    def inspect_dynamic(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        url = str(arguments.get("url", "")).strip()
        if not self._safe_url(url):
            raise ValueError("research_inspect_dynamic requires an http(s) URL")
        if self.dynamic_inspector is not None:
            inspected = self.dynamic_inspector(url)
            if inspected is not None:
                return dict(inspected)
        extracted = self.extract_page({"url": url})
        return {
            "url": extracted["url"],
            "dynamic": extracted["dynamic_hint"],
            "rendering": "static_fallback",
            "text": extracted["text"],
            "note": "No browser runtime is configured; page scripts were detected but not executed.",
        }

    def _optional_browser_inspector(self, url: str) -> Mapping[str, Any] | None:
        """Use Playwright when installed; never pretend static HTML was rendered."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                page.goto(url, wait_until="networkidle", timeout=int(self.timeout_seconds * 1000))
                result = {
                    "url": page.url,
                    "dynamic": True,
                    "rendering": "playwright",
                    "title": page.title(),
                    "text": page.locator("body").inner_text(timeout=5000)[:30000],
                }
                browser.close()
                return result
        except Exception as exc:
            return {"url": url, "dynamic": False, "rendering": "browser_error", "error": str(exc)}

    def collect_sources(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        objective = str(arguments.get("objective", "")).strip()
        queries = tuple(str(item).strip() for item in arguments.get("queries", ()) if str(item).strip())
        urls = tuple(str(item).strip() for item in arguments.get("urls", ()) if str(item).strip())
        request = ResearchRequest(objective=objective, queries=queries, urls=urls, max_sources=int(arguments.get("max_sources", 5)), inspect_dynamic=bool(arguments.get("inspect_dynamic", True)))
        sources: list[ResearchSource] = []
        for query in request.queries:
            payload = self.search({"query": query, "limit": request.max_sources})
            for item in payload["results"]:
                url = item.get("url", "")
                if not self._safe_url(url) or any(source.url == url for source in sources):
                    continue
                extracted = self.extract_page({"url": url})
                source = self._source(url, item.get("title", ""), item.get("snippet", ""), extracted.get("text", ""), False)
                sources.append(source)
                if len(sources) >= request.max_sources:
                    break
            if len(sources) >= request.max_sources:
                break
        for url in request.urls:
            if len(sources) >= request.max_sources or not self._safe_url(url):
                continue
            extracted = self.extract_page({"url": url})
            dynamic = False
            if request.inspect_dynamic and extracted.get("dynamic_hint"):
                dynamic_result = self.inspect_dynamic({"url": url})
                extracted["text"] = dynamic_result.get("text", extracted.get("text", ""))
                dynamic = dynamic_result.get("rendering") != "static_fallback"
            sources.append(self._source(url, extracted.get("title", ""), "", extracted.get("text", ""), dynamic))
        citations = tuple(source.citation for source in sources)
        evidence = "\n\n".join(f"[{index}] {source.title or source.url}: {source.extracted_text[:1200]}" for index, source in enumerate(sources, 1))
        return ResearchResult(objective, tuple(sources), evidence, citations, request.queries, sum(1 for source in sources if source.dynamic)).to_dict()

    @staticmethod
    def _source(url: str, title: str, snippet: str, text: str, dynamic: bool) -> ResearchSource:
        source_id = "src_" + hashlib.sha256(url.encode()).hexdigest()[:12]
        citation = f"[{source_id}] {title or url} ({url})"
        return ResearchSource(source_id, url, title, snippet, text[:12000], citation, dynamic=dynamic)

    @staticmethod
    def _safe_url(url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class ResearchToolset:
    """Adapter exposing research capabilities as executor ToolSpecs."""

    def __init__(self, tools: ResearchTools | None = None) -> None:
        self.tools = tools or ResearchTools()

    def handlers(self) -> dict[str, Callable[[Mapping[str, Any]], Any]]:
        return {
            "research_search": self.tools.search,
            "research_extract_page": self.tools.extract_page,
            "research_inspect_dynamic": self.tools.inspect_dynamic,
            "research_collect_sources": self.tools.collect_sources,
        }

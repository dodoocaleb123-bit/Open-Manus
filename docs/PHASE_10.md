# Phase 10: Qwen 2.5:3b research workflows

Phase 10 connects the research specialist role (`qwen2.5:3b`) to search, page extraction, source collection, citation tracking, and optional browser-backed dynamic-page inspection.

## Research sequence

```text
DeepSeek defines research objective
  ↓
DeepSeek issues a research tool command
  ↓
Search and page tools gather evidence
  ↓
DeepSeek delegates the source bundle to Qwen 2.5:3b
  ↓
Qwen compares and summarizes evidence
  ↓
Structured findings and citations return to DeepSeek
  ↓
DeepSeek verifies, synthesizes, and presents the answer
```

DeepSeek remains responsible for deciding whether research is needed, whether more evidence is required, and what final answer the user receives.

## Registered tools

The local API registers these tools with the controlled executor:

- `research_search`: DuckDuckGo HTML search with bounded results
- `research_extract_page`: HTTP page fetch and readable-text extraction
- `research_inspect_dynamic`: Playwright-backed inspection when Playwright and a browser are installed; otherwise an explicit static fallback
- `research_collect_sources`: deduplicated search/page collection with bounded evidence excerpts and citations

All research tools use HTTP(S) URLs, bounded response sizes, timeouts, and a declared user agent. The tools do not claim that dynamic rendering occurred when only static extraction was available.

## Source and citation records

Each collected source receives:

- Stable `src_<sha256-prefix>` identifier
- URL
- Title and search snippet
- Extracted evidence text
- Citation string
- Dynamic-rendering flag

The collected result includes `sources`, `citations`, `search_queries`, `evidence_summary`, and `dynamic_pages_inspected` so DeepSeek can verify and cite the evidence.

## Qwen handoff

DeepSeek should delegate a bounded evidence-review task with the source bundle in `inputs`:

```json
{
  "type": "delegate_to_model",
  "arguments": {
    "role": "research",
    "objective": "Compare the collected evidence",
    "inputs": {
      "sources": [],
      "citations": []
    },
    "expected_output": "citation-backed findings and disagreements"
  }
}
```

When source or citation inputs are present, the executor wraps Qwen's response as:

```json
{
  "specialist": "qwen2.5:3b",
  "analysis_type": "research_evidence_review",
  "findings": {},
  "citations": [],
  "source_count": 0
}
```

Qwen is never presented as the primary assistant. Its result is returned to DeepSeek for verification, synthesis, and final delivery.

## Browser automation boundary

Playwright is optional for local deployment. Install Playwright and its browser separately if dynamic JavaScript-rendered pages are required. Without it, the system uses static HTML extraction and marks the result as `static_fallback`; it does not invent rendered content.

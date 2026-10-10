#!/usr/bin/env python3
"""Benchmark local Ollama models: time-to-first-token, completion, throughput, and loaded size.

Example:
  python scripts/benchmark_ollama.py --models deepseek-r1:7b qwen2.5-coder:7b gemma3:4b --runs 3
Uses only the Python standard library; prompts and results stay on the configured Ollama host.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from typing import Any


def get_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def once(base_url: str, model: str, prompt: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url + "/api/generate",
        data=json.dumps({"model": model, "prompt": prompt, "stream": True, "keep_alive": "5m"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    first_token = None
    final: dict[str, Any] = {}
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            if not line.strip():
                continue
            item = json.loads(line.decode("utf-8"))
            if item.get("error"):
                raise RuntimeError(str(item["error"]))
            if first_token is None and item.get("response"):
                first_token = time.perf_counter()
            if item.get("done"):
                final = item
    completed = time.perf_counter()
    result = {
        "model": model,
        "time_to_first_token_seconds": round((first_token or completed) - started, 3),
        "completion_seconds": round(completed - started, 3),
        "eval_count": final.get("eval_count"),
        "tokens_per_second": round(final["eval_count"] / (final["eval_duration"] / 1e9), 2)
        if final.get("eval_count") and final.get("eval_duration") else None,
        "load_duration_seconds": round(final["load_duration"] / 1e9, 3)
        if final.get("load_duration") is not None else None,
        "success": True,
    }
    try:
        running = get_json(base_url + "/api/ps")
        loaded = next((item for item in running.get("models", []) if item.get("name") == model), {})
        result["loaded_model_size_bytes"] = loaded.get("size")
        result["loaded_model_size_vram_bytes"] = loaded.get("size_vram")
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        result["loaded_model_size_bytes"] = None
        result["loaded_model_size_vram_bytes"] = None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434", help="Ollama API base URL")
    parser.add_argument("--models", nargs="+", required=True, help="Installed Ollama model tags")
    parser.add_argument("--runs", type=int, default=3, help="Runs per model")
    parser.add_argument("--timeout", type=float, default=180, help="Timeout per model request")
    parser.add_argument("--prompt", default="Reply with exactly: READY", help="Identical prompt for each model")
    parser.add_argument("--output", default="ollama-benchmark.json", help="Report file path")
    args = parser.parse_args()
    if args.runs < 1 or args.timeout <= 0:
        parser.error("--runs and --timeout must be positive")
    base_url = args.base_url.rstrip("/")
    try:
        tags = get_json(base_url + "/api/tags")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        parser.error(f"Cannot reach Ollama at {base_url}: {exc}")
    installed = {item.get("name") for item in tags.get("models", [])}
    missing = [model for model in args.models if model not in installed]
    if missing:
        parser.error("Models not installed: " + ", ".join(missing))

    results = []
    for model in args.models:
        for run_number in range(1, args.runs + 1):
            try:
                result = once(base_url, model, args.prompt, args.timeout)
                result["run"] = run_number
            except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
                result = {"model": model, "run": run_number, "success": False, "error": str(exc)}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

    summary = {}
    for model in args.models:
        good = [r for r in results if r["model"] == model and r["success"]]
        summary[model] = {
            "successful_runs": len(good),
            "failed_runs": args.runs - len(good),
            "median_time_to_first_token_seconds": round(statistics.median([r["time_to_first_token_seconds"] for r in good]), 3) if good else None,
            "median_completion_seconds": round(statistics.median([r["completion_seconds"] for r in good]), 3) if good else None,
            "median_tokens_per_second": round(statistics.median([r["tokens_per_second"] for r in good if r["tokens_per_second"] is not None]), 2) if any(r["tokens_per_second"] is not None for r in good) else None,
        }
    report = {"base_url": base_url, "runs_per_model": args.runs, "prompt": args.prompt, "results": results, "summary": summary}
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)
        output.write("\n")
    print(f"Saved benchmark report to {args.output}")
    return 0 if all(value["successful_runs"] for value in summary.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

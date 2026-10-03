#!/usr/bin/env python3
"""Check Phase 1 prerequisites without making model calls or exposing secrets."""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = [
    ROOT / "backend/openmanus-core/main.py",
    ROOT / "backend/openmanus-core/requirements.txt",
    ROOT / "docs/ARCHITECTURE.md",
    ROOT / ".env.example",
]
MODEL_VARS = {
    "controller": "DEEPSEEK_CONTROLLER_MODEL",
    "research": "RESEARCH_LLM_MODEL",
    "coder": "CODER_LLM_MODEL",
    "vision": "VISION_LLM_MODEL",
    "creative": "CREATIVE_LLM_MODEL",
}


def check_paths() -> bool:
    ok = True
    for path in REQUIRED_PATHS:
        exists = path.exists()
        print(f"{'OK' if exists else 'MISSING'} path: {path.relative_to(ROOT)}")
        ok &= exists
    return ok


def check_ollama() -> bool:
    base_url = os.getenv("DEEPSEEK_CONTROLLER_BASE_URL", "http://localhost:11434/v1")
    endpoint = base_url.removesuffix("/v1") + "/api/tags"
    try:
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            print(f"OK Ollama reachable: {endpoint} ({response.status})")
            return True
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"WARN Ollama not reachable: {endpoint} ({exc})")
        return False


def main() -> int:
    print("Open-Manus Phase 1 environment check")
    paths_ok = check_paths()
    print("\nConfigured model roles from environment (names only):")
    for role, variable in MODEL_VARS.items():
        print(f"- {role}: {os.getenv(variable, '<not loaded; copy .env.example to .env>')}")
    ollama_ok = check_ollama()
    print("\nResult:")
    print("Repository foundation is ready." if paths_ok else "Repository foundation is incomplete.")
    if not ollama_ok:
        print("Ollama is optional for static Phase 1 validation; start it before model testing.")
    return 0 if paths_ok else 1


if __name__ == "__main__":
    sys.exit(main())

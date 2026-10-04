#!/usr/bin/env python3
"""Run the local Open-Manus Phase 8 API and GUI."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from api.server import serve  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the local Open-Manus GUI")
    parser.add_argument("--host", default=os.getenv("OPENMANUS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OPENMANUS_PORT", "8000")))
    args = parser.parse_args()
    serve(args.host, args.port)

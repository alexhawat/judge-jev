#!/usr/bin/env python3
"""Checkout entrypoint for the packaged judge_jev evaluation API."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "src"))

from judge_jev import evaluation as _evaluation  # noqa: E402
from judge_jev.evaluation import *  # noqa: F403,E402
from judge_jev.evaluation import main  # noqa: E402


def __getattr__(name: str):
    return getattr(_evaluation, name)

if __name__ == "__main__":
    raise SystemExit(main())

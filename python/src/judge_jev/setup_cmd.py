"""Runtime setup: choose python or rust."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from judge_jev.paths import repo_root, runtime_config_path


def _prompt_runtime() -> str:
    env = os.environ.get("JUDGE_JEV_RUNTIME", "").strip().lower()
    if env in {"python", "rust"}:
        return env
    print("Select judge-jev runtime:")
    print("  1) python (uv + loguru + typesafe-sdk)")
    print("  2) rust (cargo + tracing + HTTP client)")
    choice = input("Enter 1 or 2 [1]: ").strip() or "1"
    return "python" if choice == "1" else "rust"


def install_python(root: Path) -> None:
    subprocess.run(["uv", "sync", "--dev"], cwd=root / "python", check=True)


def install_rust(root: Path) -> None:
    subprocess.run(["cargo", "build", "--release"], cwd=root / "rust", check=True)


def write_runtime(runtime: str) -> Path:
    path = runtime_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{runtime}\n", encoding="utf-8")
    return path


def run_setup() -> int:
    root = repo_root()
    runtime = _prompt_runtime()
    if runtime == "python":
        install_python(root)
    else:
        install_rust(root)
    cfg = write_runtime(runtime)
    print(f"Runtime '{runtime}' configured at {cfg}")
    print("Set TYPESAFE_API_KEY for live judging, or use --mock in CI.")
    return 0

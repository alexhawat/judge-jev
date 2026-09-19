"""Repository path resolution."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("JUDGE_JEV_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "shared" / "rubrics").is_dir():
            return parent
    return Path.cwd()


def rubrics_dir() -> Path:
    return repo_root() / "shared" / "rubrics"


def schemas_dir() -> Path:
    return repo_root() / "shared" / "schemas"


def runtime_config_path() -> Path:
    return repo_root() / ".judge-jev" / "runtime"

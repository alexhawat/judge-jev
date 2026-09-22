"""Repository path resolution."""

from __future__ import annotations

import os
from pathlib import Path

MARKER = Path("shared") / "rubrics"
PACKAGE_ASSETS = Path(__file__).resolve().parent / "assets"


def _search_up(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / MARKER).is_dir():
            return parent
    return None


def repo_root() -> Path:
    """Locate the repo that owns shared/rubrics.

    JUDGE_JEV_ROOT wins when set; the wrapper script always sets it. Otherwise look
    upward from this file (works from a source checkout) and then from the working
    directory (works when judge_jev is installed into site-packages, where the
    source tree is nowhere near the rubrics).
    """
    env = os.environ.get("JUDGE_JEV_ROOT")
    if env:
        return Path(env).resolve()
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        found = _search_up(start)
        if found is not None:
            return found
    return Path.cwd()


def rubrics_dir() -> Path:
    if os.environ.get("JUDGE_JEV_ROOT"):
        return repo_root() / "shared" / "rubrics"
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        found = _search_up(start)
        if found is not None:
            return found / "shared" / "rubrics"
    packaged = PACKAGE_ASSETS / "rubrics"
    return packaged if packaged.is_dir() else repo_root() / "shared" / "rubrics"


def schemas_dir() -> Path:
    if os.environ.get("JUDGE_JEV_ROOT"):
        return repo_root() / "shared" / "schemas"
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        found = _search_up(start)
        if found is not None:
            return found / "shared" / "schemas"
    packaged = PACKAGE_ASSETS / "schemas"
    return packaged if packaged.is_dir() else repo_root() / "shared" / "schemas"


def runtime_config_path() -> Path:
    return repo_root() / ".judge-jev" / "runtime"

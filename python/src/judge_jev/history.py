"""Opt-in local result history. Raw judged inputs are never stored here."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from judge_jev.canonical import strict_json_loads
from judge_jev.models import JudgeJevError

RETENTION_LIMIT = 100
HISTORY_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}$")


def history_dir() -> Path:
    override = os.environ.get("JUDGE_JEV_HISTORY_DIR")
    if override:
        return Path(override).expanduser()
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "judge-jev" / "history"


def _safe_id(result_id: str) -> str:
    if not isinstance(result_id, str) or HISTORY_ID.fullmatch(result_id) is None:
        raise JudgeJevError("history id is not a judge-jev result id")
    return result_id


def _owned_files() -> list[Path]:
    root = history_dir()
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise JudgeJevError(f"history directory is not a regular directory: {root}")
    owned: list[Path] = []
    for path in root.iterdir():
        if HISTORY_ID.fullmatch(path.stem) is None or path.suffix != ".json":
            continue
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode):
            owned.append(path)
    return sorted(owned, reverse=True)


def _read_owned(path: Path) -> dict[str, Any] | None:
    try:
        payload = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("history_id") != path.stem
        or not isinstance(payload.get("saved_at"), str)
        or not isinstance(payload.get("result"), dict)
    ):
        return None
    return payload


def save_result(result: dict[str, Any]) -> str:
    root = history_dir()
    if root.is_symlink():
        raise JudgeJevError(f"history directory may not be a symlink: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    result_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    payload = {"history_id": result_id, "saved_at": datetime.now(UTC).isoformat(), "result": result}
    fd, temporary = tempfile.mkstemp(prefix=".result-", suffix=".tmp", dir=root)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / f"{result_id}.json")
        try:
            (root / f"{result_id}.json").chmod(0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    valid_owned = [path for path in _owned_files() if _read_owned(path) is not None]
    for stale in valid_owned[RETENTION_LIMIT:]:
        stale.unlink(missing_ok=True)
    return result_id


def list_entries() -> list[dict[str, Any]]:
    entries = []
    for path in _owned_files():
        payload = _read_owned(path)
        if payload is None:
            continue
        result = payload["result"]
        entries.append(
            {
                "history_id": payload["history_id"],
                "saved_at": payload["saved_at"],
                "verdict": result.get("verdict"),
                "rubric_id": result.get("rubric_id"),
                "mock": result.get("mock"),
            }
        )
    return entries


def load_entry(result_id: str) -> dict[str, Any]:
    path = history_dir() / f"{_safe_id(result_id)}.json"
    if path.is_symlink() or path not in _owned_files():
        raise JudgeJevError(f"history result {result_id} is not an owned regular file")
    payload = _read_owned(path)
    if payload is None:
        raise JudgeJevError(f"history result {result_id} is malformed")
    return payload


def delete_entry(result_id: str) -> None:
    path = history_dir() / f"{_safe_id(result_id)}.json"
    if path.is_symlink() or path not in _owned_files() or _read_owned(path) is None:
        raise JudgeJevError(f"history result not found: {result_id}")
    path.unlink()


def delete_all() -> int:
    paths = [path for path in _owned_files() if _read_owned(path) is not None]
    for path in paths:
        path.unlink()
    return len(paths)

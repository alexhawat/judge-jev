"""Bounded, opt-in capture of judgment evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import random
import stat
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from judge_jev.canonical import canonical_json
from judge_jev.models import JudgmentResult, Rubric
from judge_jev.rubric import rubric_content_hash

CAPTURE_VERSION = 1
OWNED_PREFIX = "judge-jev-capture-v1-"
OWNED_SUFFIX = ".jsonl"

# Sampling quotas belong to the process, not to one short-lived writer. This
# matters for library callers that make many judgments in one process and create a
# writer for each. The PID check resets inherited state after fork.
_RULE_COUNTS_LOCK = threading.Lock()
_RULE_COUNTS_PID = os.getpid()
_PROCESS_RULE_COUNTS: dict[object, int] = {}


def _process_rule_counts() -> dict[object, int]:
    global _RULE_COUNTS_PID, _PROCESS_RULE_COUNTS
    pid = os.getpid()
    with _RULE_COUNTS_LOCK:
        if pid != _RULE_COUNTS_PID:
            _RULE_COUNTS_PID = pid
            _PROCESS_RULE_COUNTS = {}
        return _PROCESS_RULE_COUNTS


def _reset_after_fork() -> None:
    global _RULE_COUNTS_LOCK, _RULE_COUNTS_PID, _PROCESS_RULE_COUNTS
    _RULE_COUNTS_LOCK = threading.Lock()
    _RULE_COUNTS_PID = os.getpid()
    _PROCESS_RULE_COUNTS = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


@dataclass
class CapturePolicy:
    audit_rate: float = 0.02
    uncertain_below: float = 0.65
    boundary_margin: float = 0.10
    per_rule_quota: int = 25
    per_rule_rate: float = 0.005
    queue_records: int = 128
    queue_bytes: int = 4 * 1024 * 1024
    record_bytes: int = 512 * 1024
    file_bytes: int = 8 * 1024 * 1024
    directory_bytes: int = 128 * 1024 * 1024
    retention_files: int = 32
    shutdown_seconds: float = 0.25

    @classmethod
    def from_env(cls) -> "CapturePolicy":
        def number(name: str, default: float) -> float:
            raw = os.environ.get(name)
            return default if raw is None else float(raw)

        policy = cls(
            audit_rate=number("JUDGE_JEV_CAPTURE_AUDIT_RATE", 0.02),
            uncertain_below=number("JUDGE_JEV_CAPTURE_UNCERTAIN_BELOW", 0.65),
            boundary_margin=number("JUDGE_JEV_CAPTURE_BOUNDARY_MARGIN", 0.10),
        )
        if not 0 <= policy.audit_rate <= 1:
            raise ValueError("JUDGE_JEV_CAPTURE_AUDIT_RATE must be in [0, 1]")
        return policy


@dataclass
class CaptureStats:
    written: int = 0
    dropped: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _numeric_margin(left: float, op: str, right: float) -> tuple[float, bool]:
    if op == ">=":
        return left - right, left >= right
    if op == ">":
        return left - right, left > right
    if op == "<=":
        return right - left, left <= right
    if op == "<":
        return right - left, left < right
    if op == "==":
        return -abs(left - right), left == right
    if op == "!=":
        return abs(left - right), left != right
    raise ValueError(op)


def rule_margins(rubric: Rubric, answers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every rule's evaluable conditions, including rules that did not fire.

    Numeric margins are positive when the condition is satisfied and negative when
    it is not. Strict operators still use signed distance, with equality recorded
    separately in ``matched``. An AND rule's margin is the minimum numeric condition
    margin. Categorical conditions are explicit and do not invent numeric distance.
    """
    out: list[dict[str, Any]] = []
    for index, rule in enumerate(rubric.rules):
        conditions: list[dict[str, Any]] = []
        numeric: list[float] = []
        evaluable = True
        matched = True
        for condition in rule.conditions:
            answer = answers.get(condition.answer)
            value = None if answer is None else answer.get(condition.field)
            item: dict[str, Any] = {
                "answer": condition.answer,
                "field": condition.field,
                "op": condition.op,
                "threshold": condition.value,
                "observed": value,
            }
            if value is None:
                item.update(kind="unevaluable", matched=None, margin=None)
                evaluable = False
                matched = False
            elif condition.field == "choice":
                condition_match = (str(value) == str(condition.value)) if condition.op == "==" else (str(value) != str(condition.value))
                item.update(kind="categorical", matched=condition_match, margin=None)
                matched = matched and condition_match
            else:
                margin, condition_match = _numeric_margin(float(value), condition.op, float(condition.value))
                item.update(kind="numeric", matched=condition_match, margin=margin)
                numeric.append(margin)
                matched = matched and condition_match
            conditions.append(item)
        out.append(
            {
                "rule": index,
                "verdict": rule.verdict,
                "default": rule.default,
                "evaluable": evaluable,
                "matched": bool(rule.default or (matched and evaluable)),
                "margin": min(numeric) if numeric else None,
                "conditions": conditions,
            }
        )
    return out


def _redact(value: Any, parts: list[str]) -> None:
    if not parts:
        return
    token = parts[0]
    array = token.endswith("[]")
    key = token[:-2] if array else token
    if not isinstance(value, dict) or key not in value:
        return
    if len(parts) == 1:
        value[key] = "[REDACTED]"
        return
    child = value[key]
    if array:
        if isinstance(child, list):
            for item in child:
                _redact(item, parts[1:])
    else:
        _redact(child, parts[1:])


def redact_state(state: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    copied = copy.deepcopy(state)
    for path in paths:
        _redact(copied, path.split("."))
    return copied


def select_reasons(
    result: JudgmentResult,
    margins: list[dict[str, Any]],
    policy: CapturePolicy,
    *,
    rng: Callable[[], float] = random.random,
    rule_counts: dict[object, int] | None = None,
    rule_namespace: str | None = None,
) -> list[str]:
    reasons: list[str] = []
    if rng() < policy.audit_rate:
        reasons.append("audit")
    if result.confidence < policy.uncertain_below:
        reasons.append("uncertain")
    deciding = set(result.deciding_answers)
    if any(
        condition["answer"] in deciding
        and condition["kind"] == "numeric"
        and abs(float(condition["margin"])) <= policy.boundary_margin
        for rule in margins
        for condition in rule["conditions"]
    ):
        reasons.append("boundary")
    if "Downgraded from" in result.routing_reason:
        reasons.append("downgraded")
    if result.verdict in ("review", "escalate"):
        reasons.append("non_pass")
    matched = next((int(item["rule"]) for item in margins if item["matched"]), None)
    # The quota is additionally probability-gated so one-shot CLI processes do
    # not reset a counter and capture 100% of traffic. It remains stratified per
    # matched rule and capped within long-running processes.
    if (
        matched is not None
        and rule_counts is not None
        and rng() < policy.per_rule_rate
    ):
        # The lock also makes the cap exact when multiple judgment threads share
        # the process. Supplying a private dict remains useful for deterministic
        # tests, and is harmless under the same lock.
        with _RULE_COUNTS_LOCK:
            rule_key: object = (rule_namespace, matched) if rule_namespace is not None else matched
            if rule_counts.get(rule_key, 0) < policy.per_rule_quota:
                reasons.append("rule_quota")
                rule_counts[rule_key] = rule_counts.get(rule_key, 0) + 1
    return reasons


def build_record(
    result: JudgmentResult,
    rubric: Rubric,
    filtered_state: dict[str, Any],
    *,
    redact_paths: list[str] | None = None,
    captured_at: str | None = None,
    runtime: dict[str, str] | None = None,
    policy: CapturePolicy | None = None,
    rng: Callable[[], float] = random.random,
    rule_counts: dict[object, int] | None = None,
) -> dict[str, Any] | None:
    policy = policy or CapturePolicy.from_env()
    margins = rule_margins(rubric, result.answers)
    reasons = select_reasons(
        result,
        margins,
        policy,
        rng=rng,
        rule_counts=rule_counts,
        rule_namespace=result.rubric_hash,
    )
    if not reasons:
        return None
    rubric_hash = rubric_content_hash(rubric)
    state_hash = "sha256:" + hashlib.sha256(canonical_json(filtered_state).encode("utf-8")).hexdigest()
    timestamp = captured_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "version": CAPTURE_VERSION,
        "captured_at": timestamp,
        "trigger_reasons": reasons,
        "rubric_id": result.rubric_id,
        "rubric_version": result.rubric_version,
        "rubric_hash": rubric_hash,
        "state_hash": state_hash,
        "runtime": runtime or result.runtime.to_dict(),
        "backend": result.backend,
        "backend_provenance": result.backend_provenance,
        "requested_model": result.requested_model,
        "model": result.model,
        "state": redact_state(filtered_state, redact_paths or []),
        "answers": result.answers,
        "usage": result.usage.to_dict(),
        "verdict": result.verdict,
        "confidence": result.confidence,
        "confidence_floor": result.confidence_floor,
        "deciding_answers": result.deciding_answers,
        "routing_reason": result.routing_reason,
        "deterministic_gates": [gate.to_dict() for gate in result.deterministic_gates],
        "rule_margins": margins,
    }


class CaptureWriter:
    """A per-process, bounded background JSONL writer."""

    def __init__(self, directory: Path, policy: CapturePolicy | None = None) -> None:
        self.directory = directory
        self.policy = policy or CapturePolicy.from_env()
        self.stats = CaptureStats()
        self.pid = os.getpid()
        self._queued_bytes = 0
        self._pending_records = 0
        self._lock = threading.Lock()
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=self.policy.queue_records)
        self._stop = threading.Event()
        self._failed = threading.Event()
        self._closed = False
        self._close_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="judge-jev-capture", daemon=True)
        self._thread.start()

    @property
    def rule_counts(self) -> dict[object, int]:
        return _process_rule_counts()

    def _prepare_directory(self) -> None:
        if self.directory.exists() and self.directory.is_symlink():
            raise OSError("capture directory must not be a symlink")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Re-check after creation to close the ordinary check/create race. The
        # capture file itself is also opened with O_NOFOLLOW below.
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise OSError("capture directory must be a real directory")
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass

    def enqueue(self, record: dict[str, Any] | None) -> bool:
        if record is None:
            return False
        if os.getpid() != self.pid:
            self.stats.dropped += 1
            return False
        try:
            raw = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        except Exception:
            self.stats.errors += 1
            return False
        if len(raw) > self.policy.record_bytes:
            self.stats.dropped += 1
            return False
        with self._lock:
            if self._closed or self._stop.is_set() or self._failed.is_set():
                self.stats.dropped += 1
                return False
            if self._queued_bytes + len(raw) > self.policy.queue_bytes:
                self.stats.dropped += 1
                return False
            try:
                self._queue.put_nowait(raw)
            except queue.Full:
                self.stats.dropped += 1
                return False
            self._queued_bytes += len(raw)
            self._pending_records += 1
        return True

    def _complete_pending(self, outcome: str) -> None:
        with self._lock:
            # close() owns records it counted as abandoned. A worker that later
            # returns from a blocked filesystem call must not count them twice.
            if self._pending_records == 0:
                return
            self._pending_records -= 1
            setattr(self.stats, outcome, getattr(self.stats, outcome) + 1)

    def _owned_files(self) -> list[tuple[Path, os.stat_result]]:
        files: list[tuple[Path, os.stat_result]] = []
        for path in self.directory.glob(f"{OWNED_PREFIX}*{OWNED_SUFFIX}"):
            try:
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and not path.is_symlink():
                    files.append((path, info))
            except OSError:
                self.stats.errors += 1
        return sorted(files, key=lambda item: (item[1].st_mtime_ns, item[0].name))

    def _enforce_bounds(self, incoming_bytes: int = 0) -> bool:
        files = self._owned_files()
        # Include every owned active file in the byte total, but only finalized
        # files in the eviction set. This protects concurrent writers while still
        # making enough room for the pending record instead of dropping forever
        # whenever the directory happens to sit exactly at its cap.
        total = self._directory_size()
        while files and (
            len(files) > self.policy.retention_files
            or total + incoming_bytes > self.policy.directory_bytes
        ):
            victim, _info = files.pop(0)
            try:
                victim.unlink()
                total = self._directory_size()
            except FileNotFoundError:
                # Another process enforcing the same cap already removed it.
                total = self._directory_size()
            except OSError:
                self.stats.errors += 1
        return total + incoming_bytes <= self.policy.directory_bytes

    def _new_file(self) -> tuple[Any, Path, int]:
        name = f"{OWNED_PREFIX}{self.pid}-{time.time_ns()}-{uuid.uuid4().hex}{OWNED_SUFFIX}.active"
        path = self.directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        return os.fdopen(fd, "ab", buffering=0), path, 0

    def _finalize(self, handle: Any, active_path: Path) -> None:
        handle.close()
        try:
            if active_path.stat().st_size == 0:
                active_path.unlink()
                return
        except FileNotFoundError:
            return
        final_path = active_path.with_suffix("")
        os.replace(active_path, final_path)

    def _directory_size(self) -> int:
        total = 0
        for path in self.directory.iterdir():
            if path.name.startswith(OWNED_PREFIX) and (
                path.name.endswith(OWNED_SUFFIX) or path.name.endswith(f"{OWNED_SUFFIX}.active")
            ):
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and not path.is_symlink():
                        total += info.st_size
                except OSError:
                    self.stats.errors += 1
        return total

    def _run(self) -> None:
        handle = None
        active_path = None
        size = 0
        active_pending = False
        prepared = False
        try:
            # All filesystem work is on the background thread; constructing a
            # writer and enqueueing on the judgment path only touch memory.
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    item = self._queue.get(timeout=0.02)
                except queue.Empty:
                    continue
                with self._lock:
                    self._queued_bytes -= len(item)
                active_pending = True
                if not prepared:
                    self._prepare_directory()
                    prepared = True
                    handle, active_path, size = self._new_file()
                if size and size + len(item) > self.policy.file_bytes:
                    self._finalize(handle, active_path)
                    self._enforce_bounds()
                    handle, active_path, size = self._new_file()
                if not self._enforce_bounds(len(item)):
                    self._complete_pending("dropped")
                    active_pending = False
                    continue
                handle.write(item)
                size += len(item)
                self._complete_pending("written")
                active_pending = False
        except Exception:
            with self._lock:
                self.stats.errors += 1
                self._failed.set()
            if active_pending:
                self._complete_pending("errors")
            while True:
                try:
                    pending = self._queue.get_nowait()
                except queue.Empty:
                    break
                if pending is not None:
                    with self._lock:
                        self._queued_bytes = max(0, self._queued_bytes - len(pending))
                    self._complete_pending("dropped")
        finally:
            if handle is not None and active_path is not None:
                try:
                    self._finalize(handle, active_path)
                except OSError:
                    self.stats.errors += 1
            if prepared:
                try:
                    self._enforce_bounds()
                except OSError:
                    self.stats.errors += 1
            self._stop.set()

    def close(self) -> CaptureStats:
        if os.getpid() != self.pid:
            # No thread survives fork except the caller. In particular, inherited
            # locks and the Thread object cannot safely be acquired or joined.
            # This process owns only its private memory copy, so account for that
            # copy and return without touching any synchronization primitive.
            self.stats.dropped += self._pending_records
            self._pending_records = 0
            self._closed = True
            return self.stats
        with self._close_lock:
            if self._closed:
                return self.stats
            with self._lock:
                self._closed = True
                self._stop.set()
        self._thread.join(self.policy.shutdown_seconds)
        if self._thread.is_alive():
            with self._lock:
                self.stats.dropped += self._pending_records
                self._pending_records = 0
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                with self._lock:
                    self._queued_bytes = max(0, self._queued_bytes - len(item))
        return self.stats


def prune(directory: Path, older_than_seconds: float, *, apply: bool = False, now: float | None = None) -> list[Path]:
    if directory.is_symlink():
        raise OSError("capture directory must not be a symlink")
    cutoff = (time.time() if now is None else now) - older_than_seconds
    selected: list[Path] = []
    if not directory.exists():
        return selected
    for path in directory.glob(f"{OWNED_PREFIX}*{OWNED_SUFFIX}"):
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and not path.is_symlink() and info.st_mtime < cutoff:
            selected.append(path)
    selected.sort()
    if apply:
        for path in selected:
            path.unlink()
    return selected

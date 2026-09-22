from __future__ import annotations

import copy
import io
import json
import os
import signal
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from judge_jev.backend import CloudflareBackend, resolve_backend
from judge_jev.canonical import CanonicalState
from judge_jev.capture import (
    CapturePolicy,
    CaptureWriter,
    build_record,
    prune,
    redact_state,
    rule_margins,
    select_reasons,
)
from judge_jev.funnel import run_judgment
from judge_jev.models import GateOutcome, JudgmentResult, Runtime, StateProjection, Usage
from judge_jev.rubric import load_rubric, rubric_content_hash

ROOT = Path(__file__).resolve().parents[2]


def test_backend_precedence_and_mock_conflicts() -> None:
    assert resolve_backend(None, mock=False, environ={}) == "typesafe"
    assert resolve_backend(None, mock=False, environ={"JUDGE_JEV_BACKEND": "cloudflare"}) == "cloudflare"
    assert resolve_backend("replay", mock=False, environ={"JUDGE_JEV_BACKEND": "cloudflare"}) == "replay"
    assert resolve_backend(None, mock=True, environ={}) == "replay"
    with pytest.raises(ValueError, match="unknown backend"):
        resolve_backend("typo", mock=False, environ={})
    with pytest.raises(ValueError, match="cannot be combined"):
        resolve_backend("typesafe", mock=True, environ={})


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.headers = {"cf-ray": "ray-1"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.payload


def test_cloudflare_documented_envelope_and_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    seen = []

    def opener(request, timeout):
        seen.append((request, timeout))
        return _Response(
            json.dumps(
                {
                    "model": "jev-1.13.0",
                    "answers": {"q": {"type": "noul", "noul": 0.9}},
                    "usage": {"input_tokens": 1, "output_tokens": 2},
                }
            ).encode()
        )

    response = CloudflareBackend(opener=opener).system_one(
        CanonicalState.of({"text": "hello"}),
        {"q": {"type": "noul", "instructions": "yes?"}},
        "jev-1.13.0",
    )
    assert response.resolved_model == "jev-1.13.0"
    assert response.request_id == "ray-1"
    body = json.loads(seen[0][0].data)
    assert body == {
        "input": {
            "questions": {"q": {"instructions": "yes?", "type": "noul"}},
            "state": '{"text":"hello"}',
        },
        "model": "typesafe/jev",
    }
    assert seen[0][1] <= 10


def test_cloudflare_malformed_success_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    calls = 0

    def opener(_request, timeout):
        nonlocal calls
        calls += 1
        return _Response(b"not-json")

    with pytest.raises(Exception, match="malformed JSON"):
        CloudflareBackend(opener=opener).system_one(
            CanonicalState.of({"x": 1}), {"q": {"type": "noul"}}, "jev-1.13.0"
        )
    assert calls == 1


@pytest.mark.parametrize("retry_after", ["nan", "inf", "-1", "Wed, 21 Oct 2015 07:28:00 GMT"])
def test_cloudflare_invalid_retry_after_falls_back_without_escaping_deadline(
    monkeypatch: pytest.MonkeyPatch, retry_after: str
) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("JUDGE_JEV_MAX_RETRIES", "1")
    now = [0.0]
    sleeps: list[float] = []
    calls = 0

    def opener(_request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                "https://example.test",
                429,
                "limited",
                {"retry-after": retry_after},
                io.BytesIO(b"limited"),
            )
        return _Response(
            b'{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.9}},'
            b'"usage":{"input_tokens":1,"output_tokens":2}}'
        )

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    CloudflareBackend(opener=opener, sleep=sleep, monotonic=lambda: now[0]).system_one(
        CanonicalState.of({"x": 1}), {"q": {"type": "noul"}}, "jev-1.13.0"
    )
    assert calls == 2
    assert sleeps == [0.5]


def test_cloudflare_retry_does_not_sleep_past_total_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    now = [0.0]
    sleeps: list[float] = []

    def opener(_request, timeout):
        assert timeout == 10.0
        now[0] = 29.8
        raise TimeoutError("slow transport")

    with pytest.raises(Exception, match="Cloudflare system_one failed"):
        CloudflareBackend(
            opener=opener,
            sleep=lambda delay: sleeps.append(delay),
            monotonic=lambda: now[0],
        ).system_one(CanonicalState.of({"x": 1}), {"q": {"type": "noul"}}, "jev-1.13.0")
    assert sleeps == []


def test_cloudflare_rejects_alias_resolution_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")

    def opener(_request, timeout):
        return _Response(
            b'{"success":true,"result":{"model":"jev-next",'
            b'"answers":{"q":{"type":"noul","noul":0.9}},'
            b'"usage":{"input_tokens":1,"output_tokens":2}}}'
        )

    with pytest.raises(Exception, match="rubric pins"):
        CloudflareBackend(opener=opener).system_one(
            CanonicalState.of({"x": 1}), {"q": {"type": "noul"}}, "jev-1.13.0"
        )


def test_recorded_backend_refuses_state_hash_and_model_provenance_drift(tmp_path: Path) -> None:
    fixture = json.loads((ROOT / "fixtures/capture-parity-input.json").read_text(encoding="utf-8"))
    record = json.loads((ROOT / "fixtures/capture-record-v1.jsonl").read_text(encoding="utf-8"))
    input_path = tmp_path / "input.json"
    replay_path = tmp_path / "capture.json"
    input_path.write_text(json.dumps(fixture["state"], ensure_ascii=False), encoding="utf-8")
    replay_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    valid = run_judgment(
        "assistant-reply", input_path, mock=False, backend_id="replay", replay_input=replay_path
    )
    assert valid.backend_provenance == "recorded_model"

    cases = [
        ({"rubric_hash": None}, "rubric_hash provenance"),
        ({"rubric_hash": "sha256:" + "0" * 64}, "rubric hash"),
        ({"state_hash": None}, "state_hash provenance"),
        ({"requested_model": None}, "model provenance"),
        ({"model": "jev-next"}, "model provenance"),
    ]
    for changes, message in cases:
        replay_path.write_text(json.dumps({**record, **changes}), encoding="utf-8")
        with pytest.raises(Exception, match=message):
            run_judgment(
                "assistant-reply", input_path, mock=False, backend_id="replay", replay_input=replay_path
            )

    replay_path.write_text(json.dumps(record), encoding="utf-8")
    changed_state = copy.deepcopy(fixture["state"])
    changed_state["reply"] = "Different evaluated content"
    input_path.write_text(json.dumps(changed_state), encoding="utf-8")
    with pytest.raises(Exception, match="state hash does not match"):
        run_judgment(
            "assistant-reply", input_path, mock=False, backend_id="replay", replay_input=replay_path
        )


def _result(**changes) -> JudgmentResult:
    values = {
        "rubric_id": "assistant-reply",
        "rubric_version": "2.0.0",
        "rubric_hash": "sha256:" + "0" * 64,
        "verdict": "pass",
        "confidence": 0.9,
        "stage": "score",
        "model": "jev-1.13.0",
        "usage": Usage(1, 2),
        "answers": {},
        "routing_reason": "matched",
        "mock": False,
        "backend": "typesafe",
        "requested_model": "jev-1.13.0",
        "backend_provenance": "live_model",
        "deciding_answers": [],
        "confidence_floor": 0.5,
        "runtime": Runtime("test", "1"),
    }
    values.update(changes)
    return JudgmentResult(**values)


def test_rule_margins_cover_nonfiring_rules_and_strict_boundaries() -> None:
    rubric = load_rubric("assistant-reply")
    answers = {
        "screen.judgeable": {"type": "noul", "noul": 0.5},
        "screen.injection": {"type": "noul", "noul": 0.2},
        "locate.harmful": {"type": "noul", "noul": 0.1},
        "route.escalate": {"type": "noul", "noul": 0.1},
        "profile.intent": {"type": "choice", "choice": "answer", "confidence": 0.9, "probabilities": {}},
        "locate.hallucination_risk": {"type": "noul", "noul": 0.1},
        "score.helpfulness": {"type": "score", "score": 2.0, "confidence": 0.9, "probabilities": {}},
        "score.coherence": {"type": "score", "score": 2.0, "confidence": 0.9, "probabilities": {}},
    }
    margins = rule_margins(rubric, answers)
    assert len(margins) == len(rubric.rules)
    assert margins[0]["margin"] == 0.0
    assert margins[0]["matched"] is False  # strict < at equality
    categorical = margins[9]["conditions"][0]
    assert categorical["kind"] == "categorical" and categorical["margin"] is None


def test_nested_redaction_does_not_mutate_input() -> None:
    state = {"steps": [{"token": "secret", "keep": 1}], "auth": {"password": "pw"}}
    original = copy.deepcopy(state)
    redacted = redact_state(state, ["steps[].token", "auth.password"])
    assert state == original
    assert redacted["steps"][0]["token"] == "[REDACTED]"
    assert redacted["auth"]["password"] == "[REDACTED]"
    assert "secret" not in json.dumps(redacted)


def test_audit_membership_is_not_erased_by_enrichment() -> None:
    result = _result(verdict="review", confidence=0.1)
    reasons = select_reasons(
        result,
        [],
        CapturePolicy(audit_rate=1.0, per_rule_rate=0.0),
        rng=lambda: 0.0,
        rule_counts={},
    )
    assert reasons == ["audit", "uncertain", "non_pass"]


def test_per_rule_quota_is_probability_gated_for_one_shot_processes() -> None:
    result = _result()
    margins = [{"rule": 1, "matched": True, "conditions": []}]
    policy = CapturePolicy(audit_rate=0.0, uncertain_below=0.0, per_rule_rate=0.005)
    assert select_reasons(result, margins, policy, rng=lambda: 0.9, rule_counts={}) == []


def test_per_rule_quota_caps_across_short_lived_writers(tmp_path: Path) -> None:
    margins = [{"rule": 997, "matched": True, "conditions": []}]
    policy = CapturePolicy(audit_rate=0.0, uncertain_below=0.0, per_rule_rate=1.0, per_rule_quota=2)
    observed = []
    for index in range(3):
        writer = CaptureWriter(tmp_path / str(index), policy)
        observed.append(
            select_reasons(_result(), margins, policy, rng=lambda: 0.0, rule_counts=writer.rule_counts)
        )
        writer.close()
    assert observed == [["rule_quota"], ["rule_quota"], []]


def test_writer_rejects_oversize_and_prunes_owned_files_only(tmp_path: Path) -> None:
    policy = CapturePolicy(record_bytes=32, audit_rate=1.0)
    writer = CaptureWriter(tmp_path, policy)
    assert writer.enqueue({"payload": "x" * 100}) is False
    stats = writer.close()
    assert stats.dropped >= 1

    old = tmp_path / "judge-jev-capture-v1-old.jsonl"
    other = tmp_path / "unrelated.jsonl"
    old.write_text("{}\n")
    other.write_text("keep")
    os.utime(old, (1, 1))
    selected = prune(tmp_path, 1, apply=True, now=time.time())
    assert old in selected and not old.exists()
    assert other.exists()


def test_directory_cap_evicts_finalized_file_and_preserves_active_writer(tmp_path: Path) -> None:
    old = tmp_path / "judge-jev-capture-v1-old.jsonl"
    other_active = tmp_path / "judge-jev-capture-v1-other.jsonl.active"
    old.write_bytes(b"x" * 90)
    other_active.write_bytes(b"y")
    policy = CapturePolicy(
        directory_bytes=128,
        record_bytes=128,
        file_bytes=128,
        shutdown_seconds=1.0,
    )
    writer = CaptureWriter(tmp_path, policy)
    assert writer.enqueue({"value": "z" * 30})
    stats = writer.close()
    assert stats.written == 1
    assert not old.exists()
    assert other_active.exists()
    assert sum(path.stat().st_size for path in tmp_path.iterdir()) <= policy.directory_bytes


def test_writer_with_no_selected_record_creates_no_empty_file(tmp_path: Path) -> None:
    target = tmp_path / "capture"
    writer = CaptureWriter(target)
    assert writer.enqueue(None) is False
    assert writer.close().to_dict() == {"written": 0, "dropped": 0, "errors": 0}
    assert not target.exists()


def test_background_setup_failure_is_counted_and_rejects_later_records(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("occupied")
    writer = CaptureWriter(target, CapturePolicy(shutdown_seconds=1.0))
    assert writer.enqueue({"first": True})
    deadline = time.monotonic() + 1
    while not writer._failed.is_set() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not writer.enqueue({"after": True})
    stats = writer.close()
    assert stats.errors >= 1
    assert stats.dropped >= 1


def test_close_is_finite_and_accounts_for_pending_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocked_prepare(_writer: CaptureWriter) -> None:
        entered.set()
        release.wait(1)

    monkeypatch.setattr(CaptureWriter, "_prepare_directory", blocked_prepare)
    writer = CaptureWriter(tmp_path, CapturePolicy(shutdown_seconds=0.01))
    assert writer.enqueue({"first": True})
    assert entered.wait(1)
    assert writer.enqueue({"second": True})
    started = time.monotonic()
    stats = writer.close()
    assert time.monotonic() - started < 0.2
    assert stats.dropped == 2
    release.set()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded.*:DeprecationWarning")
def test_inherited_writer_rejects_child_process_enqueue(tmp_path: Path) -> None:
    writer = CaptureWriter(tmp_path)
    read_fd, write_fd = os.pipe()
    writer._close_lock.acquire()
    child = os.fork()
    if child == 0:  # pragma: no cover - assertions happen in parent
        os.close(read_fd)
        signal.alarm(2)
        accepted = writer.enqueue({"child": True})
        stats = writer.close()
        os.write(write_fd, (b"1" if accepted else b"0") + str(stats.dropped).encode())
        os.close(write_fd)
        os._exit(0)
    writer._close_lock.release()
    os.close(write_fd)
    observed = os.read(read_fd, 16)
    os.close(read_fd)
    os.waitpid(child, 0)
    assert observed.startswith(b"0")
    writer.close()


def test_capture_record_canonical_and_redacted(tmp_path: Path) -> None:
    rubric = load_rubric("assistant-reply")
    result = run_judgment(
        "assistant-reply",
        Path("../fixtures/assistant-reply-pass.json"),
        mock=True,
        backend_id="replay",
    )
    record = build_record(
        result,
        rubric,
        {"prompt": "secret", "reply": "ok"},
        redact_paths=["prompt"],
        captured_at="2026-01-01T00:00:00Z",
        runtime={"name": "fixture", "version": "1"},
        policy=CapturePolicy(audit_rate=1.0, per_rule_rate=0.0),
        rng=lambda: 0.0,
        rule_counts={},
    )
    assert record is not None
    assert record["state"]["prompt"] == "[REDACTED]"
    assert record["usage"] == {"input_tokens": 120, "output_tokens": 45}
    assert record["deterministic_gates"] == [gate.to_dict() for gate in result.deterministic_gates]
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
    assert "secret" not in raw


def _fixture_result(data: dict) -> JudgmentResult:
    values = dict(data)
    values["usage"] = Usage(**values["usage"])
    values["runtime"] = Runtime(**values["runtime"])
    values["state_projection"] = StateProjection(**values["state_projection"])
    values["deterministic_gates"] = [GateOutcome(**gate) for gate in values["deterministic_gates"]]
    return JudgmentResult(**values)


def test_capture_record_bytes_match_shared_cross_runtime_fixture() -> None:
    fixture = json.loads((ROOT / "fixtures/capture-parity-input.json").read_text(encoding="utf-8"))
    rubric = load_rubric(fixture["rubric_id"])
    result = _fixture_result(fixture["result"])
    assert result.rubric_hash == rubric_content_hash(rubric)
    record = build_record(
        result,
        rubric,
        fixture["state"],
        redact_paths=fixture["redact_paths"],
        captured_at=fixture["captured_at"],
        runtime=fixture["runtime"],
        policy=CapturePolicy(audit_rate=1.0, per_rule_rate=0.0),
        rng=lambda: 0.0,
        rule_counts={},
    )
    assert record["usage"] == fixture["result"]["usage"]
    assert record["deterministic_gates"] == fixture["result"]["deterministic_gates"]
    actual = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    expected = (ROOT / "fixtures/capture-record-v1.jsonl").read_bytes()
    assert b"private@example.test" not in actual
    assert b"secret-token" not in actual
    assert actual == expected


def test_capture_record_fixture_validates_against_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    capture_schema = json.loads(
        (ROOT / "shared/schemas/capture-record.schema.json").read_text(encoding="utf-8")
    )
    result_schema = json.loads(
        (ROOT / "shared/schemas/judgment-result.schema.json").read_text(encoding="utf-8")
    )
    registry = referencing.Registry().with_resource(
        result_schema["$id"], referencing.Resource.from_contents(result_schema)
    )
    record = json.loads((ROOT / "fixtures/capture-record-v1.jsonl").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(capture_schema, registry=registry).validate(record)

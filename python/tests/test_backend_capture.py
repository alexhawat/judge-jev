from __future__ import annotations

import copy
import json
import os
import time
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
from judge_jev.models import JudgmentResult, Runtime, Usage
from judge_jev.rubric import load_rubric


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


def _result(**changes) -> JudgmentResult:
    values = {
        "rubric_id": "assistant-reply",
        "rubric_version": "2.0.0",
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
    categorical = margins[4]["conditions"][0]
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
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
    assert "secret" not in raw

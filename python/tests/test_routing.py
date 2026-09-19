"""Routing and mock funnel tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge_jev.funnel import replay_judgment, run_judgment
from judge_jev.routing import route_verdict
from judge_jev.rubric import load_rubric


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def test_assistant_reply_mock_pass():
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    assert result.rubric_id == "assistant-reply"
    assert result.mock is True
    assert result.verdict in {"pass", "review", "fail", "escalate", "skip"}
    assert 0 <= result.confidence <= 1
    assert result.model == "jev-1.13.0"


def test_injection_escalates():
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-injection.json", mock=True)
    assert result.verdict == "escalate"
    assert "injection" in result.routing_reason.lower()


def test_agent_trajectory_mock():
    result = run_judgment("agent-trajectory", FIXTURES / "agent-trajectory-pass.json", mock=True)
    assert result.rubric_id == "agent-trajectory"
    assert result.mock is True


def test_replay_preserves_answers():
    saved = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    payload = saved.to_dict()
    replayed = replay_judgment(payload)
    assert replayed.answers == saved.answers
    assert replayed.verdict == saved.verdict


def test_route_skip_not_judgeable():
    rubric = load_rubric("assistant-reply")
    answers = {"screen.judgeable": {"type": "noul", "noul": 0.2}}
    verdict, reason, stage = route_verdict(rubric, answers)
    assert verdict == "skip"
    assert stage == "screen"


def test_judgment_result_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).resolve().parents[2] / "shared" / "schemas" / "judgment-result.schema.json"
    schema = json.loads(schema_path.read_text())
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    jsonschema.validate(result.to_dict(), schema)

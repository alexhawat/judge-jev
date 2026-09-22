"""Judgment contracts, fail-closed behavior, and optional tracing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge_jev.cli import EXIT_ERROR, main
from judge_jev.funnel import run_judgment
from judge_jev.gates import evaluate_gates, GateContext
from judge_jev.logfire_tracing import TracingConfig, configure_tracing
from judge_jev.models import JudgeJevError
from judge_jev.rubric import load_rubric

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
SCHEMA = json.loads((REPO / "shared" / "schemas" / "judgment-result.schema.json").read_text())


def test_judgment_result_records_contract_fields():
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    data = result.to_dict()

    assert data["rubric_id"] == "assistant-reply"
    assert data["rubric_version"] == load_rubric("assistant-reply").version
    assert data["model"] == "jev-1.13.0"
    assert data["mock"] is True
    assert "paths" in data["state_projection"]
    assert data["state_projection"]["hash"].startswith("sha256:")
    assert isinstance(data["deterministic_gates"], list)
    assert len(data["deterministic_gates"]) >= 4
    for gate in data["deterministic_gates"]:
        assert gate["outcome"] in {"pass", "fail", "skip"}
        assert gate["gate_id"] and gate["reason"]


def test_injection_fixture_marks_heuristic_gate_fail():
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-injection.json", mock=True)
    injection = next(g for g in result.deterministic_gates if g.gate_id == "injection_heuristic")
    assert injection.outcome == "fail"


def test_api_failure_is_operational_not_verdict(monkeypatch, capsys):
    from judge_jev import funnel

    class BrokenEngine:
        def system_one(self, state, questions, model):
            raise RuntimeError("upstream timeout")

    monkeypatch.setattr(funnel, "get_engine", lambda *_a, **_k: BrokenEngine())
    code = main(["run", "--rubric", "assistant-reply", "--input", str(FIXTURES / "assistant-reply-pass.json"), "--mock"])
    assert code == EXIT_ERROR
    assert code not in (0, 1)
    err = capsys.readouterr().err
    assert "judge-jev:" in err
    assert "Traceback" not in err


def test_empty_api_response_is_operational(monkeypatch):
    from judge_jev import funnel
    from judge_jev.models import Usage

    class EmptyEngine:
        def system_one(self, state, questions, model):
            return {}, Usage(1, 1), "req", model

    monkeypatch.setattr(funnel, "get_engine", lambda *_a, **_k: EmptyEngine())
    with pytest.raises(JudgeJevError, match="no answers"):
        run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)


def test_typesafe_transport_error_wraps_as_judge_jev_error(monkeypatch):
    from judge_jev.typesafe_client import LiveEngine

    class BrokenClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def system_one(self, **kwargs):
            raise TimeoutError("deadline exceeded")

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr("judge_jev.typesafe_client.TypeSafeClient", lambda **kwargs: BrokenClient())

    engine = LiveEngine()
    with pytest.raises(JudgeJevError, match="system_one failed"):
        engine.system_one(
            type("S", (), {"text": "{}", "value": {}})(),
            {},
            "jev-1.13.0",
        )


def test_missing_judge_answer_escalates_not_pass():
    rubric = load_rubric("assistant-reply")
    answers = {
        "screen.judgeable": {"type": "noul", "noul": 0.95},
        # screen.injection deliberately missing
        "locate.hallucination_risk": {"type": "noul", "noul": 0.1},
        "locate.harmful": {"type": "noul", "noul": 0.02},
        "route.escalate": {"type": "noul", "noul": 0.05},
        "profile.intent": {"type": "choice", "choice": "answer", "confidence": 0.9, "probabilities": {"answer": 0.9}},
        "score.helpfulness": {"type": "score", "score": 3.0, "confidence": 0.9, "probabilities": {"3": 0.9}},
        "score.coherence": {"type": "score", "score": 3.0, "confidence": 0.9, "probabilities": {"3": 0.9}},
    }
    ctx = GateContext(
        rubric=rubric,
        filtered_state={"prompt": "x", "reply": "y"},
        answers=answers,
        verdict="escalate",
        confidence=0.0,
        confidence_floor=rubric.confidence_floor,
    )
    completeness = next(g for g in evaluate_gates(ctx) if g.gate_id == "answer_completeness")
    assert completeness.outcome == "fail"


def test_tracing_extra_absent_is_noop():
    config = TracingConfig.from_cli(tracing=True, tracing_to="logfire")
    assert configure_tracing(config) is False


def test_tracing_config_rejects_unknown_sink():
    with pytest.raises(ValueError, match="unsupported tracing sink"):
        TracingConfig.from_cli(tracing=True, tracing_to="otel")


def test_tracing_flag_without_extra_still_runs_mock(capsys):
    code = main(
        [
            "run",
            "--rubric",
            "assistant-reply",
            "--input",
            str(FIXTURES / "assistant-reply-pass.json"),
            "--mock",
            "--tracing",
        ]
    )
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "pass"


def test_mock_result_validates_against_extended_schema():
    jsonschema = pytest.importorskip("jsonschema")
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    jsonschema.validate(result.to_dict(), SCHEMA)

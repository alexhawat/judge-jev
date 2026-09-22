from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import judge_jev.cli as cli_module
from judge_jev.backend import JEV_CAPABILITIES, BackendResponse
from judge_jev.cli import main
from judge_jev.gepa_tuning import (
    BudgetLedger,
    CallLimitedLM,
    JevGEPAAdapter,
    SemanticCache,
    _live_evaluator,
    instruction_proposal,
)
from judge_jev.models import JudgeJevError
from judge_jev.tuning import load_cost_policy


@dataclass
class FakeBatch:
    outputs: list
    scores: list
    trajectories: list | None = None


def _complete_answers() -> dict:
    return {
        "screen.judgeable": {"type": "noul", "noul": 0.9},
        "screen.injection": {"type": "noul", "noul": 0.1},
        "profile.intent": {
            "type": "choice", "choice": "answer", "confidence": 0.9, "probabilities": {}
        },
        "locate.hallucination_risk": {"type": "noul", "noul": 0.1},
        "locate.harmful": {"type": "noul", "noul": 0.1},
        "score.helpfulness": {
            "type": "score", "score": 3.1, "confidence": 0.9, "probabilities": {}
        },
        "score.coherence": {
            "type": "score", "score": 3.2, "confidence": 0.9, "probabilities": {}
        },
        "route.escalate": {"type": "noul", "noul": 0.1},
    }


def _cases(path: Path) -> None:
    rows = []
    number = 0
    for split in ("train", "heldout", "frozen-redteam"):
        for expected in ("pass", "fail", "review", "escalate"):
            rows.append(
                {
                    "id": f"case-{number}",
                    "rubric_id": "assistant-reply",
                    "split": split,
                    "category": "adversarial" if split == "frozen-redteam" else "ordinary",
                    "expected_verdict": expected,
                    "input": {"prompt": "p", "reply": "r", "context": "c"},
                }
            )
            number += 1
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_budget_reserves_before_calls_and_never_crosses_caps() -> None:
    ledger = BudgetLedger(max_metric_calls=1, max_task_tokens=100, max_tokens_per_call=60)
    ledger.reserve()
    ledger.settle(40)
    assert ledger.metric_calls == 1 and ledger.actual_tokens == 40
    with pytest.raises(JudgeJevError, match="metric-call budget"):
        ledger.reserve()
    too_small = BudgetLedger(max_metric_calls=2, max_task_tokens=50, max_tokens_per_call=60)
    with pytest.raises(JudgeJevError, match="task-token budget"):
        too_small.reserve()


def test_semantic_cache_is_hashed_atomic_and_recovers_corruption(tmp_path: Path) -> None:
    cache = SemanticCache(tmp_path, max_bytes=1024)
    key = cache.key({"state_hash": "abc", "criteria": ["secret phrase"], "backend": "typesafe"})
    assert "secret" not in key
    cache.put(key, {"answers": {"q": 1}})
    assert cache.get(key) == {"answers": {"q": 1}}
    path = tmp_path / f"judge-jev-gepa-v1-{key}.json"
    path.write_text("broken")
    assert cache.get(key) is None
    assert not path.exists()


def test_adapter_returns_real_disagreement_feedback_and_counts_calls() -> None:
    ledger = BudgetLedger(10, 1000, 100)

    def evaluator(row, _instruction):
        return {
            "verdict": "pass",
            "deciding_answers": ["score.helpfulness"],
            "routing_reason": "threshold matched",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }

    adapter = JevGEPAAdapter(
        "score.helpfulness", evaluator, load_cost_policy(), ledger, FakeBatch
    )
    row = {"id": "danger", "expected_verdict": "escalate"}
    batch = adapter.evaluate([row], {"score.helpfulness": "wording"}, capture_traces=True)
    assert len(batch.outputs) == len(batch.scores) == len(batch.trajectories) == 1
    assert batch.scores[0] == pytest.approx(-(200.0 + 5 * 0.001 / 1000))
    reflective = adapter.make_reflective_dataset(
        {"score.helpfulness": "wording"}, batch, ["score.helpfulness"]
    )
    assert "Expected escalate; got pass" in reflective["score.helpfulness"][0]["Feedback"]
    assert ledger.metric_calls == 1 and ledger.actual_tokens == 5


def test_adapter_budget_exhaustion_and_missing_usage_are_systemic() -> None:
    exhausted = JevGEPAAdapter(
        "q",
        lambda _row, _instruction: {"verdict": "pass", "usage": {"input_tokens": 1, "output_tokens": 1}},
        load_cost_policy(),
        BudgetLedger(0, 10, 10),
        FakeBatch,
    )
    with pytest.raises(JudgeJevError, match="budget exhausted"):
        exhausted.evaluate([{"id": "x", "expected_verdict": "pass"}], {"q": "text"})

    missing = JevGEPAAdapter(
        "q",
        lambda _row, _instruction: {"verdict": "pass", "usage": {}},
        load_cost_policy(),
        BudgetLedger(1, 10, 10),
        FakeBatch,
    )
    with pytest.raises(JudgeJevError, match="usage"):
        missing.evaluate([{"id": "x", "expected_verdict": "pass"}], {"q": "text"})


def test_cached_evidence_keeps_same_expected_token_objective() -> None:
    calls = 0

    def evaluator(_row, _instruction):
        nonlocal calls
        calls += 1
        return {
            "verdict": "pass",
            "usage": {"input_tokens": 2, "output_tokens": 3},
            "newly_billed_tokens": 5 if calls == 1 else 0,
            "newly_billed_model_call": calls == 1,
        }

    ledger = BudgetLedger(2)
    adapter = JevGEPAAdapter("q", evaluator, load_cost_policy(), ledger, FakeBatch)
    row = {"id": "x", "expected_verdict": "pass"}
    first = adapter.evaluate([row], {"q": "text"})
    cached = adapter.evaluate([row], {"q": "text"})
    assert first.scores == cached.scores
    assert ledger.actual_tokens == 5
    assert ledger.provider_calls == 1


def test_instruction_dry_run_imports_no_gepa_and_calls_no_model(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _cases(cases)
    report = instruction_proposal(
        "assistant-reply",
        "score.helpfulness",
        cases,
        live=False,
        metric_budget=0,
        minimum_support=1,
    )
    assert report["api_calls"] == 0
    assert report["live"] is False
    assert report["preserved"]["question_type"] == "score"


def test_instruction_corpus_refuses_other_rubric_before_evaluator(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _cases(cases)
    rows = [json.loads(line) for line in cases.read_text().splitlines()]
    rows[0]["rubric_id"] = "agent-trajectory"
    cases.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(JudgeJevError, match="another rubric"):
        instruction_proposal(
            "assistant-reply",
            "score.helpfulness",
            cases,
            live=True,
            budget_mode="calls",
            metric_budget=60,
            reflection_call_budget=1,
            reflection_model=lambda _prompt: "unused",
            minimum_support=1,
            evaluator=lambda *_args: pytest.fail("evaluator must not run"),
        )


def test_unsupported_hard_spend_mode_refuses_before_any_model_call(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _cases(cases)
    with pytest.raises(JudgeJevError, match="hard-spend mode is unavailable"):
        instruction_proposal(
            "assistant-reply",
            "score.helpfulness",
            cases,
            live=True,
            budget_mode="hard-spend",
            metric_budget=20,
            reflection_call_budget=2,
            task_token_cap=2000,
            max_tokens_per_call=100,
            reflection_cost_cap=1.0,
            reflection_model="unused/model",
            minimum_support=1,
            optimize_fn=lambda **_kwargs: pytest.fail("optimizer must not run"),
            batch_factory=FakeBatch,
        )


def test_reflection_call_budget_reserves_before_single_and_batch_calls() -> None:
    prompts = []

    def lm(prompt):
        prompts.append(prompt)
        return "ok"

    limited = CallLimitedLM(lm, 2)
    assert limited("one") == "ok"
    with pytest.raises(JudgeJevError, match="reflection-call budget"):
        limited.batch_complete([[{"role": "user", "content": "two"}], [{"role": "user", "content": "three"}]])
    assert prompts == ["one"]


def test_live_evaluator_runs_validation_routing_and_gates(monkeypatch, tmp_path: Path) -> None:
    from judge_jev.canonical import CanonicalState
    from judge_jev.rubric import load_rubric
    from judge_jev.typesafe_client import MockEngine, build_questions

    rubric = load_rubric("assistant-reply")
    state = CanonicalState.of({"prompt": "p", "reply": "r", "context": {}})
    answers, usage, request_id, model = MockEngine().system_one(
        state, build_questions(rubric), rubric.model
    )

    calls = 0

    class FakeBackend:
        capabilities = JEV_CAPABILITIES

        def system_one(self, *_args):
            nonlocal calls
            calls += 1
            return BackendResponse(
                answers, usage, request_id, rubric.model, model, "live_model"
            )

    monkeypatch.setattr("judge_jev.gepa_tuning.make_backend", lambda _backend: FakeBackend())
    cache = SemanticCache(tmp_path / "cache")
    evaluate = _live_evaluator("assistant-reply", "score.helpfulness", "typesafe", cache)
    output = evaluate(
        {"id": "x", "input": {"prompt": "p", "reply": "r", "context": {}}},
        "Judge helpfulness precisely.",
    )
    assert output["verdict"] in {"pass", "fail", "review", "escalate", "skip"}
    assert output["routing_trace"]["matched_rule_id"]
    assert output["deterministic_gates"]
    cached = evaluate(
        {"id": "x", "input": {"prompt": "p", "reply": "r", "context": {}}},
        "Judge helpfulness precisely.",
    )
    assert calls == 1
    assert cached["newly_billed_tokens"] == 0
    stored = json.loads(next((tmp_path / "cache").glob("judge-jev-gepa-v1-*.json")).read_text())
    assert "answers" in stored and "verdict" not in stored


def test_fake_gepa_contract_budget_and_independent_gates(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _cases(cases)
    calls = {}

    class Result:
        def __init__(self) -> None:
            self.best_candidate = {"score.helpfulness": "Clearer proposed instruction."}
            self.total_metric_calls = 1
            self.best_score = 0.8
            self.candidates = [{"score.helpfulness": "old"}, self.best_candidate]

    def optimize_fn(**kwargs):
        calls.update(kwargs)
        kwargs["adapter"].evaluate(
            [kwargs["trainset"][0]], kwargs["seed_candidate"], capture_traces=True
        )
        return Result()

    def evaluator(row, instruction):
        # Proposal and baseline both preserve the expected verdict, so the frozen
        # red-team exact per-case gate passes.
        return {
            "rubric_id": "assistant-reply",
            "rubric_version": "3.0.0",
            "verdict": row["expected_verdict"],
            "deciding_answers": ["score.helpfulness"],
            "routing_reason": f"evaluated {instruction}",
            "answers": _complete_answers(),
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    evaluator.hard_token_limit = 100

    report = instruction_proposal(
        "assistant-reply",
        "score.helpfulness",
        cases,
        live=True,
        budget_mode="calls",
        metric_budget=60,
        reflection_call_budget=2,
        reflection_model=lambda _prompt: "```Clearer proposed instruction.```",
        minimum_support=1,
        optimize_fn=optimize_fn,
        batch_factory=FakeBatch,
        evaluator=evaluator,
    )
    assert calls["max_metric_calls"] == 24  # 60 minus baseline + two all-split candidate gates
    assert "max_reflection_cost" not in calls
    assert calls["candidate_selection_strategy"] == "pareto"
    assert report["api_calls"] == 37
    assert report["metric_call_limit"] == 60
    assert report["reflection_call_limit"] == 2
    assert report["hard_token_or_dollar_cap"] is False
    assert report["redteam_hard_gate_passed"] is True
    assert "Clearer proposed instruction" in report["diff"]
    assert "criteria:" in report["diff"]


def test_actual_pinned_gepa_optimize_exercises_adapter_and_reflection() -> None:
    gepa = pytest.importorskip("gepa")
    from gepa.core.adapter import EvaluationBatch

    def evaluator(_row, instruction):
        return {
            "verdict": "pass" if instruction == "better" else "review",
            "deciding_answers": [],
            "routing_reason": "offline fake",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    adapter = JevGEPAAdapter(
        "q", evaluator, load_cost_policy(), BudgetLedger(10, 100, 10), EvaluationBatch
    )
    row = {"id": "one", "expected_verdict": "pass"}
    result = gepa.optimize(
        seed_candidate={"q": "old"},
        trainset=[row],
        valset=[row],
        adapter=adapter,
        reflection_lm=lambda _prompt: "```better```",
        reflection_minibatch_size=1,
        max_metric_calls=4,
        max_reflection_cost=1.0,
        cache_evaluation=True,
        display_progress_bar=False,
        seed=1,
    )
    assert result.best_candidate == {"q": "better"}
    assert 4 <= result.total_metric_calls <= 10
    assert adapter.ledger.metric_calls == result.total_metric_calls


def test_public_call_budget_mode_runs_actual_pinned_gepa_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("gepa")
    litellm = pytest.importorskip("litellm")
    cases = tmp_path / "cases.jsonl"
    _cases(cases)
    monkeypatch.setenv("JUDGE_JEV_MAX_RETRIES", "7")
    completions = []

    def completion(**kwargs):
        completions.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="```better```"),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=17, completion_tokens=3),
        )

    monkeypatch.setattr(litellm, "completion", completion)
    monkeypatch.setattr(litellm, "completion_cost", lambda **_kwargs: 0.125)

    def evaluator(row, instruction):
        predicted = row["expected_verdict"] if instruction == "better" else "review"
        return {
            "rubric_id": "assistant-reply",
            "rubric_version": "3.0.0",
            "verdict": predicted,
            "deciding_answers": ["score.helpfulness"],
            "routing_reason": f"offline {instruction}",
            "answers": _complete_answers(),
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    original_instruction_proposal = instruction_proposal

    def offline_instruction_proposal(*args, **kwargs):
        kwargs["evaluator"] = evaluator
        return original_instruction_proposal(*args, **kwargs)

    monkeypatch.setattr(cli_module, "instruction_proposal", offline_instruction_proposal)
    code = main(
        [
            "tune",
            "instructions",
            "--rubric",
            "assistant-reply",
            "--question",
            "score.helpfulness",
            "--set",
            str(cases),
            "--budget",
            "60",
            "--live",
            "--budget-mode",
            "calls",
            "--reflection-call-budget",
            "1",
            "--reflection-model",
            "fake/offline",
            "--min-support",
            "1",
            "--max-gate-candidates",
            "2",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    report = json.loads(captured.out)
    assert report["status"] == "proposal generated for review"
    assert report["api_calls"] <= 60
    assert report["reflection_calls"] == 1
    assert report["reflection_tokens"] == 20
    assert report["reflection_token_measurement"] == "provider-reported post-response"
    assert report["reflection_cost_usd"] == 0.125
    assert report["reflection_cost_measurement"] == "LiteLLM post-response estimate"
    assert completions and completions[0]["model"] == "fake/offline"
    assert completions[0]["num_retries"] == 0
    assert report["provider_retries"] == 0
    assert report["hard_token_or_dollar_cap"] is False
    assert "better" in report["diff"]
    assert report["candidate_proposals"]
    assert all(
        set(candidate["metrics"]["per_split"]) == {"train", "heldout", "frozen-redteam"}
        for candidate in report["candidate_proposals"]
    )
    assert __import__("os").environ["JUDGE_JEV_MAX_RETRIES"] == "7"
    assert "Iteration" in captured.err

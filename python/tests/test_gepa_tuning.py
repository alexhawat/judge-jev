from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from judge_jev.gepa_tuning import (
    BudgetLedger,
    JevGEPAAdapter,
    SemanticCache,
    instruction_proposal,
)
from judge_jev.models import JudgeJevError
from judge_jev.tuning import load_cost_policy


@dataclass
class FakeBatch:
    outputs: list
    scores: list
    trajectories: list | None = None


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
    path = tmp_path / f"{key}.json"
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
    assert batch.scores[0] < 0.01
    reflective = adapter.make_reflective_dataset(
        {"score.helpfulness": "wording"}, batch, ["score.helpfulness"]
    )
    assert "Expected escalate; got pass" in reflective["score.helpfulness"][0]["Feedback"]
    assert ledger.metric_calls == 1 and ledger.actual_tokens == 5


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


def test_fake_gepa_contract_budget_and_independent_gates(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _cases(cases)
    calls = {}

    class Result:
        best_candidate = {"score.helpfulness": "Clearer proposed instruction."}
        total_metric_calls = 1
        best_score = 0.8
        candidates = [{"score.helpfulness": "old"}, best_candidate]

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
            "verdict": row["expected_verdict"],
            "deciding_answers": ["score.helpfulness"],
            "routing_reason": f"evaluated {instruction}",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }

    report = instruction_proposal(
        "assistant-reply",
        "score.helpfulness",
        cases,
        live=True,
        metric_budget=20,
        task_token_cap=2000,
        max_tokens_per_call=100,
        reflection_cost_cap=1.0,
        reflection_model="fake/model",
        minimum_support=1,
        optimize_fn=optimize_fn,
        batch_factory=FakeBatch,
        evaluator=evaluator,
    )
    assert calls["max_metric_calls"] == 4  # 20 minus 2 * (4 heldout + 4 redteam)
    assert calls["max_reflection_cost"] == 1.0
    assert calls["candidate_selection_strategy"] == "pareto"
    assert report["api_calls"] == 17
    assert report["redteam_hard_gate_passed"] is True
    assert "Clearer proposed instruction" in report["diff"]
    assert "criteria:" in report["diff"]

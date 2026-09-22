from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge_jev.models import JudgeJevError
from judge_jev.rubric import load_rubric
from judge_jev.tuning import (
    Candidate,
    candidate_diff,
    discover_parameters,
    evaluate_candidate,
    group_advantages,
    join_corpus,
    load_cost_policy,
    pareto_front,
    threshold_search,
    validate_support,
)


def _row(case_id: str, split: str, expected: str) -> dict:
    return {
        "id": case_id,
        "rubric_id": "assistant-reply",
        "split": split,
        "category": "ordinary",
        "expected_verdict": expected,
        "result": {
            "answers": {},
            "usage": {"input_tokens": 10, "output_tokens": 2},
            "hint": expected,
        },
    }


def test_join_refuses_missing_and_extra_results() -> None:
    cases = [{k: v for k, v in _row("one", "train", "pass").items() if k != "result"}]
    with pytest.raises(JudgeJevError, match="no result"):
        join_corpus(cases, [])
    with pytest.raises(JudgeJevError, match="unknown case ids"):
        join_corpus(cases, [{"case_id": "one", "result": {"answers": {}}}, {"case_id": "extra", "result": {"answers": {}}}])


def test_support_refusal_is_per_split_and_class() -> None:
    rows = [_row("one", "train", "pass")]
    with pytest.raises(JudgeJevError, match="heldout:pass=0"):
        validate_support(rows, ["pass", "escalate"], 1)


def test_asymmetric_false_pass_cost_ranks_below_review() -> None:
    rubric = load_rubric("assistant-reply")
    rows = [_row("danger", "train", "escalate")]
    policy = load_cost_policy()
    pass_metrics = evaluate_candidate(rubric, rows, policy, reroute=lambda _r, _x: "pass")
    review_metrics = evaluate_candidate(rubric, rows, policy, reroute=lambda _r, _x: "review")
    assert pass_metrics["negative_expected_cost_reward"] < review_metrics["negative_expected_cost_reward"]
    assert pass_metrics["splits"]["train"]["safety_support"] == 1


def test_group_advantage_zero_variance_is_safe() -> None:
    assert group_advantages([2.0, 2.0, 2.0]) == [0.0, 0.0, 0.0]


def _metrics(recall: float, precision: float, review: float, tokens: int) -> dict:
    return {
        "splits": {
            "heldout": {
                "safety_recall": recall,
                "pass_precision": precision,
                "human_review_volume": review,
                "tokens": tokens,
            }
        }
    }


def test_pareto_dominance_keeps_tradeoffs() -> None:
    best = Candidate({}, _metrics(1.0, 1.0, 0.1, 10), 0.0, True)
    dominated = Candidate({}, _metrics(0.5, 0.5, 0.3, 20), 0.0, True)
    tradeoff = Candidate({}, _metrics(1.0, 0.9, 0.05, 8), 0.0, True)
    rejected = Candidate({}, _metrics(1.0, 1.0, 0.0, 0), 0.0, False)
    frontier = pareto_front([best, dominated, tradeoff, rejected])
    assert best in frontier and tradeoff in frontier
    assert dominated not in frontier and rejected not in frontier


def test_diff_is_proposal_only_and_original_stays_byte_identical() -> None:
    root = Path(__file__).resolve().parents[2]
    rubric_path = root / "shared" / "rubrics" / "assistant-reply.yaml"
    original = rubric_path.read_bytes()
    rubric = load_rubric("assistant-reply")
    parameters = discover_parameters(rubric)
    values = {parameter.name: parameter.initial for parameter in parameters}
    values[parameters[0].name] = min(parameters[0].high, parameters[0].initial + 0.01)
    candidate = Candidate(values, {}, 0.0, True)
    diff = candidate_diff(rubric_path, parameters, candidate)
    assert "+++" in diff and "version:" in diff
    assert rubric_path.read_bytes() == original


def test_threshold_search_is_seeded_and_labels_synthetic_demo(tmp_path: Path) -> None:
    cases = []
    results = []
    for index, (split, expected) in enumerate(
        [("train", "pass"), ("heldout", "pass"), ("frozen-redteam", "escalate")]
    ):
        case_id = f"case-{index}"
        cases.append({k: v for k, v in _row(case_id, split, expected).items() if k != "result"})
        results.append({"case_id": case_id, "result": _row(case_id, split, expected)["result"]})
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text("".join(json.dumps(row) + "\n" for row in cases))
    results_path.write_text("".join(json.dumps(row) + "\n" for row in results))

    def reroute(_rubric, result):
        return result["hint"]

    first = threshold_search(
        "assistant-reply",
        cases_path,
        results_path,
        seed=7,
        iterations=2,
        candidates_per_iteration=4,
        minimum_support=2,
        synthetic_demo=True,
        reroute=reroute,
    )
    second = threshold_search(
        "assistant-reply",
        cases_path,
        results_path,
        seed=7,
        iterations=2,
        candidates_per_iteration=4,
        minimum_support=2,
        synthetic_demo=True,
        reroute=reroute,
    )
    assert first == second
    assert first["api_calls"] == 0
    assert first["quality_claim_allowed"] is False
    assert first["support_warning"]

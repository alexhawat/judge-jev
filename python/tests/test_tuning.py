from __future__ import annotations

import json
from pathlib import Path

import pytest

from judge_jev.models import JudgeJevError
from judge_jev.rubric import load_rubric, rubric_content_hash
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


def _row(case_id: str, split: str, expected: str) -> dict:
    return {
        "id": case_id,
        "rubric_id": "assistant-reply",
        "split": split,
        "category": "ordinary",
        "expected_verdict": expected,
        "input": {},
        "result": {
            "rubric_id": "assistant-reply",
            "rubric_version": "3.0.0",
            "answers": _complete_answers(),
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


def test_join_refuses_wrong_or_same_version_drifted_rubric_evidence() -> None:
    row = _row("one", "train", "pass")
    cases = [{key: value for key, value in row.items() if key != "result"}]
    result = row["result"]
    result["rubric_hash"] = rubric_content_hash(load_rubric("assistant-reply"))
    wrong_id = {**result, "rubric_id": "agent-trajectory"}
    with pytest.raises(JudgeJevError, match="result rubric_id"):
        join_corpus(cases, [{"case_id": "one", "result": wrong_id}], rubric_id="assistant-reply")
    drifted = {**result, "rubric_hash": "sha256:" + "0" * 64}
    with pytest.raises(JudgeJevError, match="rubric_hash"):
        join_corpus(cases, [{"case_id": "one", "result": drifted}], rubric_id="assistant-reply")
    result.pop("rubric_hash")
    with pytest.raises(JudgeJevError, match="legacy evidence"):
        join_corpus(cases, [{"case_id": "one", "result": result}], rubric_id="assistant-reply")
    assert join_corpus(
        cases,
        [{"case_id": "one", "result": result}],
        rubric_id="assistant-reply",
        allow_legacy_provenance=True,
    )


def test_support_refusal_is_per_split_and_class() -> None:
    rows = [_row("one", "train", "pass")]
    with pytest.raises(JudgeJevError, match="heldout:pass=0"):
        validate_support(rows, ["pass", "escalate"], 1)


def test_policy_and_support_configuration_reject_invalid_values(tmp_path: Path) -> None:
    with pytest.raises(JudgeJevError, match="at least 1"):
        validate_support([], ["pass"], 0)
    with pytest.raises(JudgeJevError, match="non-empty"):
        validate_support([], [], 1)
    invalid = tmp_path / "cost.yaml"
    invalid.write_text(
        "costs:\n  actual_fail_predicted_pass: .nan\nrequired_classes: [pass]\n",
        encoding="utf-8",
    )
    with pytest.raises(JudgeJevError, match="finite"):
        load_cost_policy(invalid)


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
    assert first["search_fit_split"] == "train"
    assert first["heldout_and_redteam_used_during_adaptation"] is False


def test_search_distribution_does_not_leak_heldout_or_redteam_labels(tmp_path: Path) -> None:
    cases = []
    results = []
    for index, split in enumerate(("train", "heldout", "frozen-redteam")):
        case_id = f"case-{index}"
        row = _row(case_id, split, "pass")
        cases.append({key: value for key, value in row.items() if key != "result"})
        results.append({"case_id": case_id, "result": row["result"]})
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text("".join(json.dumps(row) + "\n" for row in cases))
    results_path.write_text("".join(json.dumps(row) + "\n" for row in results))

    def reroute(_rubric, result):
        return result["hint"]

    first = threshold_search(
        "assistant-reply", cases_path, results_path, iterations=2,
        candidates_per_iteration=4, minimum_support=2, synthetic_demo=True, reroute=reroute,
    )
    results[1]["result"]["hint"] = "fail"
    results[2]["result"]["hint"] = "review"
    results_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    second = threshold_search(
        "assistant-reply", cases_path, results_path, iterations=2,
        candidates_per_iteration=4, minimum_support=2, synthetic_demo=True, reroute=reroute,
    )
    assert first["iterations_report"] == second["iterations_report"]


def test_redteam_exact_baseline_and_unknown_usage_block_proposals(tmp_path: Path) -> None:
    cases = []
    results = []
    for index, (split, expected, predicted) in enumerate(
        (("train", "pass", "pass"), ("heldout", "pass", "pass"), ("frozen-redteam", "fail", "review"))
    ):
        row = _row(f"case-{index}", split, expected)
        row["baseline_verdict"] = expected
        row["result"]["hint"] = predicted
        cases.append({key: value for key, value in row.items() if key != "result"})
        results.append({"case_id": row["id"], "result": row["result"]})
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text("".join(json.dumps(row) + "\n" for row in cases))
    results_path.write_text("".join(json.dumps(row) + "\n" for row in results))

    report = threshold_search(
        "assistant-reply", cases_path, results_path, iterations=1,
        candidates_per_iteration=2, minimum_support=2, synthetic_demo=True,
        reroute=lambda _rubric, result: result["hint"],
    )
    assert report["pareto_frontier"] == []
    assert report["quality_claim_allowed"] is False

    results[0]["result"]["usage"] = {"input_tokens": None, "output_tokens": None}
    results_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    with pytest.raises(JudgeJevError, match="unknown token usage"):
        threshold_search(
            "assistant-reply", cases_path, results_path, iterations=1,
            candidates_per_iteration=2, minimum_support=2, synthetic_demo=True,
            reroute=lambda _rubric, result: result["hint"],
        )

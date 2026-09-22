from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "evaluation" / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("judge_jev_evaluate", MODULE_PATH)
assert SPEC and SPEC.loader
evaluation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluation
SPEC.loader.exec_module(evaluation)


def test_saved_smoke_fixture_reports_full_agreement_without_quality_claim() -> None:
    dataset = ROOT / "evaluation" / "datasets" / "smoke.jsonl"
    cases = evaluation.validate_cases(evaluation.read_jsonl(dataset))
    results = evaluation.index_results(evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl"))
    report = evaluation.evaluate(cases, results, evaluation.read_dataset_metadata(dataset))
    assert report["dataset_kind"] == "synthetic-curated-smoke"
    assert report["agreement"] == 1.0
    assert report["macro_f1"] == 1.0
    assert report["false_pass"] == {"count": 0, "denominator": 4, "rate": 0.0}
    assert report["review_burden"] == 0.25
    assert report["redteam_regression"]["hard_gate_passed"] is True
    assert report["score_fit"]["nearest_level_fit"] == 1.0
    assert report["score_fit"]["fit"] == 1.0 - report["score_fit"]["normalized_mae"]
    assert report["usage"]["assistant-reply"]["calls"] == 4
    assert report["provenance_modes"] == {"mock_pipeline": 8}
    assert any("not real-world judge accuracy" in item for item in report["limitations"])
    assert report["per_split"]["frozen-redteam"]["labeled"] == 2
    assert report["per_split"]["frozen-redteam"]["evaluated"] == 2


def test_known_confusion_matrix_and_expensive_false_pass_are_separate() -> None:
    cases = evaluation.validate_cases([
        {"id": "a", "rubric_id": "assistant-reply", "split": "heldout", "expected_verdict": "fail", "input": {}},
        {"id": "b", "rubric_id": "assistant-reply", "split": "heldout", "expected_verdict": "pass", "input": {}},
        {"id": "c", "rubric_id": "assistant-reply", "split": "heldout", "expected_verdict": "review", "input": {}},
    ])
    rows = evaluation.index_results([
        {"case_id": "a", "result": {"verdict": "pass", "confidence": 0.9, "answers": {}, "usage": {}, "mock": False}},
        {"case_id": "b", "result": {"verdict": "pass", "confidence": 0.8, "answers": {}, "usage": {}, "mock": False}},
        {"case_id": "c", "result": {"verdict": "review", "confidence": 0.4, "answers": {}, "usage": {}, "mock": False}},
    ])
    report = evaluation.evaluate(cases, rows)
    assert report["confusion_matrix"]["fail"]["pass"] == 1
    assert report["agreement"] == 2 / 3
    assert report["false_pass"] == {"count": 1, "denominator": 1, "rate": 1.0}
    assert report["dataset_kind"] == "unknown"
    assert report["per_verdict"]["pass"]["precision"] == 0.5
    assert report["per_verdict"]["fail"]["recall"] == 0.0
    assert report["per_verdict"]["fail"]["labeled_support"] == 1
    assert report["per_verdict"]["fail"]["evaluated_support"] == 1
    assert report["redteam_regression"]["status"] == "unavailable"
    assert report["redteam_regression"]["hard_gate_passed"] is False


def test_frozen_redteam_false_pass_is_a_hard_gate(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    dataset.write_text(json.dumps({"id": "red", "rubric_id": "assistant-reply", "split": "frozen-redteam", "expected_verdict": "escalate", "input": {}}) + "\n")
    results.write_text(json.dumps({"case_id": "red", "result": {"verdict": "pass", "answers": {}, "usage": {}, "mock": False}}) + "\n")
    assert evaluation.main(["--dataset", str(dataset), "--results", str(results)]) == 2


def test_redteam_fail_to_review_is_also_a_regression() -> None:
    cases = evaluation.validate_cases([
        {"id": "red", "rubric_id": "assistant-reply", "split": "frozen-redteam", "expected_verdict": "fail", "input": {}},
    ])
    rows = evaluation.index_results([
        {"case_id": "red", "result": {"verdict": "review", "answers": {}, "usage": {}, "mock": False}},
    ])
    gate = evaluation.evaluate(cases, rows)["redteam_regression"]
    assert gate["status"] == "failed"
    assert gate["regressions"] == [{"case_id": "red", "baseline": "fail", "predicted": "review"}]


def test_invalid_redteam_result_cannot_satisfy_gate() -> None:
    cases = evaluation.validate_cases([
        {"id": "red", "rubric_id": "assistant-reply", "split": "frozen-redteam", "expected_verdict": "fail", "input": {}},
    ])
    rows = evaluation.index_results([
        {"case_id": "red", "result": {"verdict": "fail", "confidence": 2.0, "answers": {}, "usage": {}}},
    ])
    report = evaluation.evaluate(cases, rows)
    assert report["counts"]["evaluated"] == 0
    assert report["redteam_regression"]["status"] == "failed"
    assert report["redteam_regression"]["evaluated_support"] == 0
    assert report["redteam_regression"]["regressions"] == [
        {"case_id": "red", "baseline": "fail", "predicted": None}
    ]


def test_missing_results_cannot_make_ci_green(tmp_path: Path) -> None:
    dataset = ROOT / "evaluation" / "datasets" / "smoke.jsonl"
    all_results = evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl")
    one_result = tmp_path / "one.jsonl"
    one_result.write_text(json.dumps(all_results[0]) + "\n")
    report = evaluation.evaluate(
        evaluation.validate_cases(evaluation.read_jsonl(dataset)),
        evaluation.index_results([all_results[0]]),
    )
    assert report["agreement"] == 1.0
    assert report["counts"]["evaluated"] == 1
    assert report["counts"]["missing_or_error"] == 7
    assert report["redteam_regression"]["status"] == "failed"
    assert evaluation.main([
        "--dataset", str(dataset),
        "--results", str(one_result),
        "--ci-min-agreement", "1.0",
        "--ci-min-macro-f1", "1.0",
    ]) == 2


def test_threshold_and_floor_sweeps_do_not_modify_yaml() -> None:
    dataset = ROOT / "evaluation" / "datasets" / "smoke.jsonl"
    results_path = ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl"
    cases = evaluation.validate_cases(evaluation.read_jsonl(dataset))
    results = evaluation.index_results(evaluation.read_jsonl(results_path))
    rubric_path = ROOT / "shared" / "rubrics" / "assistant-reply.yaml"
    before = rubric_path.read_bytes()
    rows = evaluation.sweep(
        cases,
        results,
        [
            "assistant-reply|locate.hallucination_risk|noul=0.6,0.7,0.8",
            "assistant-reply|confidence_floor=0.4,0.5,0.6",
        ],
    )
    assert len(rows) == 6
    assert all("macro_f1" in row and "review_burden" in row for row in rows)
    assert all("per_verdict" in row and "per_split" in row for row in rows)
    assert all(row["distribution"]["rule_fires"] for row in rows)
    assert all(
        set(row["distribution"]["rule_fires"]).issubset(
            {f"rule:{index:03d}" for index in range(100)}
        )
        for row in rows
    )
    assert rubric_path.read_bytes() == before


def test_sweep_replays_historical_injection_gate() -> None:
    cases = evaluation.validate_cases(evaluation.read_jsonl(ROOT / "evaluation" / "datasets" / "smoke.jsonl"))
    raw = evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl")
    raw[0]["result"]["deterministic_gates"] = [
        {"gate_id": "injection_heuristic", "outcome": "fail", "reason": "recorded marker"}
    ]
    rows = evaluation.sweep(
        cases,
        evaluation.index_results(raw),
        ["assistant-reply|locate.hallucination_risk|noul=0.7"],
    )
    assert rows[0]["per_split"]["train"]["confusion_matrix"]["pass"]["escalate"] == 1


def test_sweep_does_not_reuse_verdict_when_saved_answers_are_missing() -> None:
    cases = evaluation.validate_cases(evaluation.read_jsonl(ROOT / "evaluation" / "datasets" / "smoke.jsonl"))
    raw = evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl")
    raw[0]["result"].pop("answers")
    row = evaluation.sweep(
        cases,
        evaluation.index_results(raw),
        ["assistant-reply|locate.hallucination_risk|noul=0.7"],
    )[0]
    assert row["mode"] == "policy_only_reroute_of_saved_answers"
    assert row["counts"]["missing_or_error"] == 1
    assert row["counts"]["evaluated"] == 7


def test_sweep_rejects_out_of_range_and_ambiguous_targets() -> None:
    cases = evaluation.validate_cases(evaluation.read_jsonl(ROOT / "evaluation" / "datasets" / "smoke.jsonl"))
    results = evaluation.index_results(evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl"))
    for spec, message in [
        ("assistant-reply|confidence_floor=-0.1", "between 0 and 1"),
        ("assistant-reply|confidence_floor=1.1", "between 0 and 1"),
        ("assistant-reply|screen.injection|noul=0.4", "ambiguous sweep target"),
    ]:
        try:
            evaluation.sweep(cases, results, [spec])
        except evaluation.EvaluationError as err:
            assert message in str(err)
        else:
            raise AssertionError(f"expected rejection for {spec}")


def test_ci_floor_flag_returns_nonzero(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    rows = evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl")
    rows[0]["result"]["verdict"] = "review"
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows))
    code = evaluation.main([
        "--dataset", str(ROOT / "evaluation" / "datasets" / "smoke.jsonl"),
        "--results", str(results),
        "--output", str(output),
        "--ci-min-agreement", "1.0",
    ])
    assert code == 2
    assert json.loads(output.read_text())["agreement"] < 1.0


def test_case_and_result_numeric_validation_rejects_nonfinite_or_wrong_score_labels() -> None:
    invalid_cases = [
        {"id": "a", "rubric_id": "assistant-reply", "split": "train", "expected_verdict": "pass", "expected_scores": {"score.helpfulness": 2.5}, "input": {}},
        {"id": "a", "rubric_id": "assistant-reply", "split": "train", "expected_verdict": "pass", "expected_scores": {"screen.injection": 1}, "input": {}},
        {"id": "a", "rubric_id": "assistant-reply", "split": "train", "expected_verdict": "pass", "expected_scores": {"score.helpfulness": 5}, "input": {}},
    ]
    for rows in invalid_cases:
        try:
            evaluation.validate_cases([rows])
        except evaluation.EvaluationError:
            pass
        else:
            raise AssertionError("invalid expected score label accepted")

    try:
        evaluation.index_results([{"case_id": "a", "latency_ms": float("nan"), "result": {}}])
    except evaluation.EvaluationError:
        pass
    else:
        raise AssertionError("non-finite latency accepted")


def test_nearest_level_score_fit_uses_fractional_values() -> None:
    assert evaluation._round_level(2.49, 4) == 2
    assert evaluation._round_level(2.5, 4) == 3
    assert evaluation._round_level(-0.4, 4) == 0
    assert evaluation._round_level(9.0, 4) == 4


def test_run_cases_rejects_exit_verdict_mismatch_and_malformed_json(monkeypatch, tmp_path: Path) -> None:
    case = {"id": "a", "rubric_id": "assistant-reply", "input": {}}
    responses = iter([
        evaluation.subprocess.CompletedProcess([], 0, '{"verdict":"fail"}', ""),
        evaluation.subprocess.CompletedProcess([], 10, '{"verdict":"pass"}', "stale"),
        evaluation.subprocess.CompletedProcess([], 0, "not json", ""),
        evaluation.subprocess.CompletedProcess([], 0, '{"verdict":"pass"}', ""),
    ])

    def fake_run(*_args, **_kwargs):
        return next(responses)

    monkeypatch.setattr(evaluation.subprocess, "run", fake_run)
    rows = [evaluation.run_cases([case], tmp_path / "judge", live=False)[0] for _ in range(4)]
    assert [row["result"] is None for row in rows] == [True, True, True, False]
    assert "does not match" in rows[0]["error"]
    assert "does not match" in rows[1]["error"]
    assert "valid JSON object" in rows[2]["error"]

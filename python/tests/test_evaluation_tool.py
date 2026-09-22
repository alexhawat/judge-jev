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
    cases = evaluation.validate_cases(evaluation.read_jsonl(ROOT / "evaluation" / "datasets" / "smoke.jsonl"))
    results = evaluation.index_results(evaluation.read_jsonl(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl"))
    report = evaluation.evaluate(cases, results)
    assert report["agreement"] == 1.0
    assert report["macro_f1"] == 1.0
    assert report["false_pass"] == {"count": 0, "denominator": 4, "rate": 0.0}
    assert report["review_burden"] == 0.25
    assert report["redteam_regression"]["hard_gate_passed"] is True
    assert report["score_fit"]["nearest_level_fit"] == 1.0
    assert report["usage"]["assistant-reply"]["calls"] == 4
    assert report["provenance_modes"] == {"mock_pipeline": 8}
    assert any("not real-world judge accuracy" in item for item in report["limitations"])


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
    assert report["per_verdict"]["pass"]["precision"] == 0.5
    assert report["per_verdict"]["fail"]["recall"] == 0.0


def test_frozen_redteam_false_pass_is_a_hard_gate(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    results = tmp_path / "results.jsonl"
    dataset.write_text(json.dumps({"id": "red", "rubric_id": "assistant-reply", "split": "frozen-redteam", "expected_verdict": "escalate", "input": {}}) + "\n")
    results.write_text(json.dumps({"case_id": "red", "result": {"verdict": "pass", "answers": {}, "usage": {}, "mock": False}}) + "\n")
    assert evaluation.main(["--dataset", str(dataset), "--results", str(results)]) == 2


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
    assert rubric_path.read_bytes() == before


def test_ci_floor_flag_returns_nonzero(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    code = evaluation.main([
        "--dataset", str(ROOT / "evaluation" / "datasets" / "smoke.jsonl"),
        "--results", str(ROOT / "evaluation" / "fixtures" / "smoke-saved-results.jsonl"),
        "--output", str(output),
        "--ci-min-macro-f1", "1.01",
    ])
    assert code == 2
    assert json.loads(output.read_text())["macro_f1"] == 1.0


def test_nearest_level_score_fit_uses_fractional_values() -> None:
    assert evaluation._round_level(2.49, 4) == 2
    assert evaluation._round_level(2.5, 4) == 3
    assert evaluation._round_level(-0.4, 4) == 0
    assert evaluation._round_level(9.0, 4) == 4

#!/usr/bin/env python3
"""Reproducible, opt-in evaluation for labeled judge-jev cases.

Saved outputs are the default. ``--run-live`` is the only path that makes model
requests; ``--run-mock`` is explicitly reported as a plumbing check, never as
evidence of judge quality. Threshold sweeps mutate in-memory rubric objects only.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import math
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python" / "src"))

from judge_jev.models import Condition, Rubric, RoutingRule  # noqa: E402
from judge_jev.routing import route_verdict  # noqa: E402
from judge_jev.rubric import load_rubric  # noqa: E402

VERDICTS = ("pass", "fail", "review", "escalate", "skip")
SPLITS = ("train", "heldout", "frozen-redteam")
EXPENSIVE_ACTUALS = frozenset({"fail", "escalate"})


class EvaluationError(ValueError):
    pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise EvaluationError(f"{path}:{number}: invalid JSON: {err.msg}") from err
        if not isinstance(value, dict):
            raise EvaluationError(f"{path}:{number}: row must be an object")
        rows.append(value)
    return rows


def validate_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    for index, row in enumerate(rows, 1):
        case_id = row.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise EvaluationError(f"case {index}: id must be a unique non-empty string")
        seen.add(case_id)
        if row.get("rubric_id") not in ("assistant-reply", "agent-trajectory"):
            raise EvaluationError(f"case {case_id}: unsupported rubric_id")
        if row.get("split") not in SPLITS:
            raise EvaluationError(f"case {case_id}: split must be one of {SPLITS}")
        if row.get("expected_verdict") not in VERDICTS:
            raise EvaluationError(f"case {case_id}: expected_verdict must be one of {VERDICTS}")
        if not isinstance(row.get("input"), dict):
            raise EvaluationError(f"case {case_id}: input must be an object")
        expected_scores = row.get("expected_scores", {})
        if not isinstance(expected_scores, dict) or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in expected_scores.values()
        ):
            raise EvaluationError(f"case {case_id}: expected_scores must contain finite numbers")
    return rows


def index_results(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in indexed:
            raise EvaluationError(f"result {index}: case_id must be a unique non-empty string")
        result = row.get("result")
        if result is not None and not isinstance(result, dict):
            raise EvaluationError(f"result {case_id}: result must be an object or null")
        if result is None and not isinstance(row.get("error"), str):
            raise EvaluationError(f"result {case_id}: missing both result and error")
        indexed[case_id] = row
    return indexed


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _round_level(score: float, maximum: int) -> int:
    return min(max(math.floor(score + 0.5), 0), maximum)


def _question_maximum(rubric: Rubric, question: str) -> int | None:
    spec = rubric.questions.get(question) or {}
    criteria = spec.get("criteria")
    return len(criteria) - 1 if spec.get("type") == "score" and isinstance(criteria, list) else None


def _rule_margin(rubric: Rubric, result: dict[str, Any]) -> float | None:
    deciding = tuple(result.get("deciding_answers") or [])
    answers = result.get("answers") or {}
    candidates = [rule for rule in rubric.rules if not rule.default and rule.answer_ids == deciding]
    for rule in candidates:
        margins = []
        usable = True
        for condition in rule.conditions:
            answer = answers.get(condition.answer) or {}
            left = answer.get(condition.field)
            if condition.field == "choice" or isinstance(left, bool) or not isinstance(left, (int, float)):
                continue
            margins.append(abs(float(left) - float(condition.value)))
        if usable and margins:
            return min(margins)
    return None


def evaluate(cases: list[dict[str, Any]], result_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    labels = list(VERDICTS)
    matrix = {actual: {predicted: 0 for predicted in labels} for actual in labels}
    disagreements = []
    expensive = []
    missing = []
    confidences: list[float] = []
    floor_downgrades = 0
    rule_fires: Counter[str] = Counter()
    margins: list[float] = []
    score_errors: list[float] = []
    score_normalized: list[float] = []
    score_fit = 0
    token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    calls_by_rubric: Counter[str] = Counter()
    latencies: dict[str, list[float]] = defaultdict(list)
    modes: Counter[str] = Counter()
    evaluated = 0

    for case in cases:
        case_id = case["id"]
        row = result_rows.get(case_id)
        if row is None or row.get("result") is None:
            missing.append({"case_id": case_id, "error": (row or {}).get("error", "missing result")})
            continue
        result = row["result"]
        predicted = result.get("verdict")
        actual = case["expected_verdict"]
        if predicted not in VERDICTS:
            missing.append({"case_id": case_id, "error": f"invalid verdict {predicted!r}"})
            continue
        matrix[actual][predicted] += 1
        evaluated += 1
        mode = "mock_pipeline" if result.get("mock") is True else str(row.get("provenance") or "saved_unspecified")
        modes[mode] += 1
        confidence = result.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence):
            confidences.append(float(confidence))
        reason = str(result.get("routing_reason") or "unknown")
        rule_fires[reason] += 1
        if "Downgraded from" in reason:
            floor_downgrades += 1
        rubric = load_rubric(case["rubric_id"])
        margin = _rule_margin(rubric, result)
        if margin is not None:
            margins.append(margin)
        usage = result.get("usage") or {}
        calls_by_rubric[case["rubric_id"]] += 1
        for field in ("input_tokens", "output_tokens"):
            value = usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                token_counts[case["rubric_id"]][field] += value
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latencies[str(row.get("latency_source") or "unspecified")].append(float(latency))

        if actual != predicted:
            detail = {
                "case_id": case_id,
                "split": case["split"],
                "actual": actual,
                "predicted": predicted,
                "deciding_answers": result.get("deciding_answers") or [],
                "confidence": confidence,
                "confidence_floor": result.get("confidence_floor"),
                "routing_reason": reason,
            }
            disagreements.append(detail)
            if actual in EXPENSIVE_ACTUALS and predicted == "pass":
                expensive.append(detail)

        answers = result.get("answers") or {}
        for question, expected in case.get("expected_scores", {}).items():
            predicted_answer = answers.get(question) or {}
            raw = predicted_answer.get("score")
            maximum = _question_maximum(rubric, question)
            if maximum is None or not isinstance(raw, (int, float)) or isinstance(raw, bool):
                continue
            error = abs(float(raw) - float(expected))
            score_errors.append(error)
            score_normalized.append(_safe_ratio(error, maximum))
            score_fit += int(_round_level(float(raw), maximum) == _round_level(float(expected), maximum))

    per_verdict = {}
    f1_values = []
    correct = 0
    for label in labels:
        tp = matrix[label][label]
        correct += tp
        fp = sum(matrix[actual][label] for actual in labels if actual != label)
        fn = sum(matrix[label][predicted] for predicted in labels if predicted != label)
        support = sum(matrix[label].values())
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        f1 = _safe_ratio(2 * precision * recall, precision + recall)
        per_verdict[label] = {"precision": precision, "recall": recall, "f1": f1, "support": support}
        if support:
            f1_values.append(f1)

    false_pass_denominator = sum(sum(matrix[actual].values()) for actual in EXPENSIVE_ACTUALS)
    frozen_expensive = [item for item in expensive if item["split"] == "frozen-redteam"]
    return {
        "schema_version": 1,
        "dataset_kind": "synthetic-curated-smoke",
        "limitations": [
            "This report measures agreement with human-authored smoke expectations, not real-world judge accuracy.",
            "Mock outputs test pipeline mechanics only and must not be cited as model efficacy.",
            "Threshold policy still requires reviewed live holdout and frozen red-team evidence.",
        ],
        "counts": {"cases": len(cases), "evaluated": evaluated, "missing_or_error": len(missing)},
        "agreement": _safe_ratio(correct, evaluated),
        "confusion_matrix": matrix,
        "per_verdict": per_verdict,
        "macro_f1": statistics.fmean(f1_values) if f1_values else 0.0,
        "false_pass": {"count": len(expensive), "denominator": false_pass_denominator, "rate": _safe_ratio(len(expensive), false_pass_denominator)},
        "review_burden": _safe_ratio(sum(matrix[actual]["review"] for actual in labels), evaluated),
        "redteam_regression": {"hard_gate_passed": not frozen_expensive, "expensive_false_passes": frozen_expensive},
        "score_fit": {
            "count": len(score_errors),
            "mae": statistics.fmean(score_errors) if score_errors else None,
            "normalized_mae": statistics.fmean(score_normalized) if score_normalized else None,
            "nearest_level_fit": _safe_ratio(score_fit, len(score_errors)),
            "rounding": "floor(score + 0.5), clamped to the rubric score range",
        },
        "distribution": {
            "confidence_mean": statistics.fmean(confidences) if confidences else None,
            "confidence_median": statistics.median(confidences) if confidences else None,
            "confidence_floor_downgrades": floor_downgrades,
            "rule_fires": dict(rule_fires.most_common()),
            "rule_margin_mean": statistics.fmean(margins) if margins else None,
            "rule_margin_median": statistics.median(margins) if margins else None,
        },
        "usage": {
            rubric: {"calls": calls_by_rubric[rubric], **dict(token_counts[rubric])}
            for rubric in sorted(calls_by_rubric)
        },
        "latency": {
            source: {"count": len(values), "mean_ms": statistics.fmean(values), "median_ms": statistics.median(values)}
            for source, values in sorted(latencies.items())
        },
        "provenance_modes": dict(modes),
        "disagreements": disagreements,
        "missing_or_error": missing,
    }


def _replace_threshold(rubric: Rubric, answer: str, field: str, value: float) -> Rubric:
    replaced = 0
    rules = []
    for rule in rubric.rules:
        conditions = []
        for condition in rule.conditions:
            if condition.answer == answer and condition.field == field:
                condition = dataclasses.replace(condition, value=value)
                replaced += 1
            conditions.append(condition)
        rules.append(dataclasses.replace(rule, conditions=tuple(conditions)))
    if not replaced:
        raise EvaluationError(f"no routing threshold reads {answer}.{field}")
    return dataclasses.replace(rubric, rules=tuple(rules))


def parse_sweep(spec: str) -> tuple[str, str, str | None, list[float]]:
    # RUBRIC|confidence_floor=0.5,0.6 or RUBRIC|ANSWER|FIELD=0.3,0.4
    left, separator, raw_values = spec.partition("=")
    if not separator:
        raise EvaluationError("--sweep needs RUBRIC|confidence_floor=... or RUBRIC|ANSWER|FIELD=...")
    pieces = left.split("|")
    if len(pieces) == 2 and pieces[1] == "confidence_floor":
        rubric_id, target, field = pieces[0], pieces[1], None
    elif len(pieces) == 3:
        rubric_id, target, field = pieces
    else:
        raise EvaluationError("invalid --sweep target")
    try:
        values = [float(item) for item in raw_values.split(",")]
    except ValueError as err:
        raise EvaluationError("--sweep values must be numbers") from err
    if not values or any(not math.isfinite(value) for value in values):
        raise EvaluationError("--sweep values must be finite")
    return rubric_id, target, field, values


def sweep(cases: list[dict[str, Any]], rows: dict[str, dict[str, Any]], specs: Iterable[str]) -> list[dict[str, Any]]:
    output = []
    for spec in specs:
        rubric_id, target, field, values = parse_sweep(spec)
        base = load_rubric(rubric_id)
        for value in values:
            rubric = copy.deepcopy(base)
            if field is None:
                floors = dict(rubric.confidence_floors)
                floors[rubric.stakes] = value
                rubric = dataclasses.replace(rubric, confidence_floors=floors)
            else:
                rubric = _replace_threshold(rubric, target, field, value)
            predicted_rows = copy.deepcopy(rows)
            for case in cases:
                if case["rubric_id"] != rubric_id:
                    continue
                row = predicted_rows.get(case["id"])
                result = (row or {}).get("result")
                if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
                    continue
                verdict, reason, stage, deciding, confidence = route_verdict(rubric, result["answers"])
                result.update(verdict=verdict, routing_reason=reason, stage=stage, deciding_answers=deciding, confidence=confidence, confidence_floor=rubric.confidence_floor)
            metrics = evaluate(cases, predicted_rows)
            output.append({"spec": spec, "value": value, "agreement": metrics["agreement"], "macro_f1": metrics["macro_f1"], "false_pass": metrics["false_pass"], "review_burden": metrics["review_burden"], "redteam_regression": metrics["redteam_regression"]})
    return output


def run_cases(cases: list[dict[str, Any]], judge: Path, *, live: bool) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        command = [str(judge.resolve()), "run", "--rubric", case["rubric_id"], "--input", "-"]
        if not live:
            command.append("--mock")
        completed = subprocess.run(command, input=json.dumps(case["input"]), text=True, capture_output=True, check=False)
        try:
            result = json.loads(completed.stdout) if completed.stdout.strip() else None
        except json.JSONDecodeError:
            result = None
        row = {"case_id": case["id"], "result": result, "provenance": "live" if live else "mock_pipeline"}
        if result is None:
            row["error"] = completed.stderr.strip() or f"judge exited {completed.returncode}"
        rows.append(row)
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate labeled judge-jev JSONL cases")
    parser.add_argument("--dataset", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--results", help="saved result JSONL; no model calls")
    source.add_argument("--run-mock", action="store_true", help="run canned offline answers; plumbing evidence only")
    source.add_argument("--run-live", action="store_true", help="explicitly make live TypeSafe model calls")
    parser.add_argument("--judge", default=str(ROOT / "scripts" / "judge-jev"))
    parser.add_argument("--output", help="write the reproducible JSON report")
    parser.add_argument("--write-results", help="write generated mock/live result JSONL")
    parser.add_argument("--sweep", action="append", default=[], metavar="SPEC")
    parser.add_argument("--ci-min-macro-f1", type=float)
    parser.add_argument("--ci-max-false-pass-rate", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases = validate_cases(read_jsonl(Path(args.dataset)))
        if args.results:
            raw_results = read_jsonl(Path(args.results))
        else:
            raw_results = run_cases(cases, Path(args.judge), live=args.run_live)
        if args.write_results:
            Path(args.write_results).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in raw_results), encoding="utf-8")
        indexed = index_results(raw_results)
        report = evaluate(cases, indexed)
        if args.sweep:
            report["sweeps"] = sweep(cases, indexed, args.sweep)
        text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        print(text, end="")
        failed = not report["redteam_regression"]["hard_gate_passed"]
        if args.ci_min_macro_f1 is not None and report["macro_f1"] < args.ci_min_macro_f1:
            failed = True
        if args.ci_max_false_pass_rate is not None and report["false_pass"]["rate"] > args.ci_max_false_pass_rate:
            failed = True
        return 2 if failed else 0
    except (EvaluationError, OSError, subprocess.SubprocessError) as err:
        print(f"evaluate: {err}", file=sys.stderr)
        return 11


if __name__ == "__main__":
    raise SystemExit(main())

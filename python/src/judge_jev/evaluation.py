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
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from judge_jev.models import GateOutcome, JudgeJevError, Rubric
from judge_jev.paths import repo_root
from judge_jev.reroute import reroute_with_rubric
from judge_jev.rubric import load_rubric

ROOT = repo_root()

VERDICTS = ("pass", "fail", "review", "escalate", "skip")
SPLITS = ("train", "heldout", "frozen-redteam")
EXPENSIVE_ACTUALS = frozenset({"fail", "escalate"})
EXIT_BY_VERDICT = {"pass": 0, "fail": 1, "review": 2, "escalate": 3, "skip": 4}


class EvaluationError(ValueError):
    pass


def _reject_json_constant(value: str) -> None:
    raise EvaluationError(f"non-finite JSON number {value} is not allowed")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            value = json.loads(line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as err:
            raise EvaluationError(f"{path}:{number}: invalid JSON: {err.msg}") from err
        if not isinstance(value, dict):
            raise EvaluationError(f"{path}:{number}: row must be an object")
        rows.append(value)
    return rows


def read_dataset_metadata(dataset_path: Path) -> dict[str, Any]:
    metadata_path = dataset_path.with_suffix(".meta.json")
    if not metadata_path.exists():
        return {}
    try:
        value = json.loads(
            metadata_path.read_text(encoding="utf-8"), parse_constant=_reject_json_constant
        )
    except json.JSONDecodeError as err:
        raise EvaluationError(f"{metadata_path}: invalid JSON: {err.msg}") from err
    if not isinstance(value, dict):
        raise EvaluationError(f"{metadata_path}: metadata must be an object")
    return value


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
        baseline = row.get("baseline_verdict", row["expected_verdict"])
        if baseline not in VERDICTS:
            raise EvaluationError(f"case {case_id}: baseline_verdict must be one of {VERDICTS}")
        expected_scores = row.get("expected_scores", {})
        if not isinstance(expected_scores, dict):
            raise EvaluationError(f"case {case_id}: expected_scores must be an object")
        rubric = load_rubric(row["rubric_id"])
        for question, value in expected_scores.items():
            maximum = _question_maximum(rubric, question)
            if maximum is None:
                raise EvaluationError(
                    f"case {case_id}: expected_scores.{question} is not a score question"
                )
            if type(value) is not int or not 0 <= value <= maximum:
                raise EvaluationError(
                    f"case {case_id}: expected_scores.{question} must be an integer from 0 to {maximum}"
                )
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
        if _contains_nonfinite(row):
            raise EvaluationError(f"result {case_id}: non-finite numbers are not allowed")
        latency = row.get("latency_ms")
        if latency is not None and (
            isinstance(latency, bool)
            or not isinstance(latency, (int, float))
            or not math.isfinite(latency)
            or latency < 0
        ):
            raise EvaluationError(f"result {case_id}: latency_ms must be a finite non-negative number")
        cost = row.get("cost_usd")
        if cost is not None and (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or cost < 0
        ):
            raise EvaluationError(f"result {case_id}: cost_usd must be a finite non-negative number")
        indexed[case_id] = row
    return indexed


def _contains_nonfinite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_nonfinite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_nonfinite(item) for item in value)
    return False


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _round_level(score: float, maximum: int) -> int:
    return min(max(math.floor(score + 0.5), 0), maximum)


def _question_maximum(rubric: Rubric, question: str) -> int | None:
    spec = rubric.questions.get(question) or {}
    criteria = spec.get("criteria")
    return len(criteria) - 1 if spec.get("type") == "score" and isinstance(criteria, list) else None


def _matrix() -> dict[str, dict[str, int]]:
    return {actual: {predicted: 0 for predicted in VERDICTS} for actual in VERDICTS}


def _classification_summary(
    matrix: dict[str, dict[str, int]], labeled: Counter[str], evaluated: int
) -> dict[str, Any]:
    per_verdict: dict[str, dict[str, float | int]] = {}
    f1_values = []
    correct = 0
    for label in VERDICTS:
        tp = matrix[label][label]
        correct += tp
        fp = sum(matrix[actual][label] for actual in VERDICTS if actual != label)
        fn = sum(matrix[label][predicted] for predicted in VERDICTS if predicted != label)
        evaluated_support = sum(matrix[label].values())
        precision = _safe_ratio(tp, tp + fp)
        recall = _safe_ratio(tp, tp + fn)
        f1 = _safe_ratio(2 * precision * recall, precision + recall)
        tn = evaluated - tp - fp - fn
        per_verdict[label] = {
            "agreement": _safe_ratio(tp + tn, evaluated),
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "labeled_support": labeled[label],
            "evaluated_support": evaluated_support,
        }
        if evaluated_support:
            f1_values.append(f1)
    return {
        "agreement": _safe_ratio(correct, evaluated),
        "macro_f1": statistics.fmean(f1_values) if f1_values else 0.0,
        "confusion_matrix": matrix,
        "per_verdict": per_verdict,
        "review_burden": _safe_ratio(
            sum(matrix[actual]["review"] for actual in VERDICTS), evaluated
        ),
        "false_pass": {
            "count": sum(matrix[actual]["pass"] for actual in EXPENSIVE_ACTUALS),
            "denominator": sum(
                sum(matrix[actual].values()) for actual in EXPENSIVE_ACTUALS
            ),
            "rate": _safe_ratio(
                sum(matrix[actual]["pass"] for actual in EXPENSIVE_ACTUALS),
                sum(sum(matrix[actual].values()) for actual in EXPENSIVE_ACTUALS),
            ),
        },
    }


def _validate_result_for_case(case: dict[str, Any], row: dict[str, Any]) -> str | None:
    result = row.get("result")
    if not isinstance(result, dict):
        return str(row.get("error") or "missing result")
    if result.get("verdict") not in VERDICTS:
        return f"invalid verdict {result.get('verdict')!r}"
    confidence = result.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        return "confidence must be a finite number from 0 to 1"
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        return "usage must be an object"
    for field in ("input_tokens", "output_tokens"):
        value = usage.get(field)
        if value is not None and (type(value) is not int or value < 0):
            return f"usage.{field} must be a non-negative integer or null"
    answers = result.get("answers") or {}
    if not isinstance(answers, dict):
        return "answers must be an object"
    rubric = load_rubric(case["rubric_id"])
    for question, answer in answers.items():
        if not isinstance(answer, dict):
            return f"answer {question} must be an object"
        score = answer.get("score")
        if score is None:
            continue
        maximum = _question_maximum(rubric, question)
        if maximum is None:
            return f"answer {question} supplies score for a non-score question"
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= maximum
        ):
            return f"answer {question}.score must be finite and between 0 and {maximum}"
    return None


def evaluate(
    cases: list[dict[str, Any]],
    result_rows: dict[str, dict[str, Any]],
    dataset_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    labels = list(VERDICTS)
    matrix = _matrix()
    split_matrices = {split: _matrix() for split in SPLITS}
    labeled = Counter(case["expected_verdict"] for case in cases)
    split_labeled = {
        split: Counter(case["expected_verdict"] for case in cases if case["split"] == split)
        for split in SPLITS
    }
    split_evaluated: Counter[str] = Counter()
    disagreements = []
    expensive = []
    missing = []
    confidences: list[float] = []
    floor_downgrades = 0
    rule_fires: Counter[str] = Counter()
    unknown_rule_identity = 0
    margins: list[float] = []
    score_errors: list[float] = []
    score_normalized: list[float] = []
    score_fit = 0
    expected_score_count = sum(len(case.get("expected_scores", {})) for case in cases)
    missing_scores: list[dict[str, str]] = []
    token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    calls_by_rubric: Counter[str] = Counter()
    latencies: dict[str, list[float]] = defaultdict(list)
    costs: dict[str, Counter[str]] = defaultdict(Counter)
    modes: Counter[str] = Counter()
    valid_case_ids: set[str] = set()
    evaluated = 0

    for case in cases:
        case_id = case["id"]
        row = result_rows.get(case_id)
        error = "missing result" if row is None else _validate_result_for_case(case, row)
        if error is not None:
            missing.append({"case_id": case_id, "split": case["split"], "error": error})
            continue
        assert row is not None
        result = row["result"]
        valid_case_ids.add(case_id)
        predicted = result.get("verdict")
        actual = case["expected_verdict"]
        matrix[actual][predicted] += 1
        split_matrices[case["split"]][actual][predicted] += 1
        split_evaluated[case["split"]] += 1
        evaluated += 1
        mode = "mock_pipeline" if result.get("mock") is True else str(row.get("provenance") or "saved_unspecified")
        modes[mode] += 1
        confidence = result.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and math.isfinite(confidence):
            confidences.append(float(confidence))
        reason = str(result.get("routing_reason") or "unknown")
        trace = result.get("routing_trace") or {}
        rule_id = trace.get("matched_rule_id") if isinstance(trace, dict) else None
        if isinstance(rule_id, str) and rule_id:
            rule_fires[rule_id] += 1
        else:
            unknown_rule_identity += 1
        if "Downgraded from" in reason:
            floor_downgrades += 1
        rubric = load_rubric(case["rubric_id"])
        trace_margin = trace.get("minimum_margin") if isinstance(trace, dict) else None
        if isinstance(trace_margin, (int, float)) and not isinstance(trace_margin, bool) and math.isfinite(trace_margin):
            margins.append(float(trace_margin))
        usage = result.get("usage") or {}
        calls_by_rubric[case["rubric_id"]] += 1
        for field in ("input_tokens", "output_tokens"):
            value = usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                token_counts[case["rubric_id"]][field] += value
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latencies[str(row.get("latency_source") or "unspecified")].append(float(latency))
        cost = row.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs[str(row.get("cost_source") or "unspecified")][case["rubric_id"]] += float(cost)

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
                missing_scores.append({"case_id": case_id, "question": question})
                continue
            error = abs(float(raw) - float(expected))
            score_errors.append(error)
            score_normalized.append(_safe_ratio(error, maximum))
            score_fit += int(_round_level(float(raw), maximum) == _round_level(float(expected), maximum))

    classification = _classification_summary(matrix, labeled, evaluated)
    false_pass_denominator = sum(sum(matrix[actual].values()) for actual in EXPENSIVE_ACTUALS)
    redteam_cases = [case for case in cases if case["split"] == "frozen-redteam"]
    redteam_regressions = []
    for case in redteam_cases:
        row = result_rows.get(case["id"])
        result = (row or {}).get("result")
        baseline = case.get("baseline_verdict", case["expected_verdict"])
        predicted = (
            result.get("verdict")
            if case["id"] in valid_case_ids and isinstance(result, dict)
            else None
        )
        if predicted != baseline:
            redteam_regressions.append(
                {"case_id": case["id"], "baseline": baseline, "predicted": predicted}
            )
    redteam_status = (
        "unavailable"
        if not redteam_cases
        else "failed" if redteam_regressions else "passed"
    )
    dataset_kind = str((dataset_metadata or {}).get("dataset_kind") or "unknown")
    limitations = [
        "This report measures agreement with supplied labels, not real-world judge accuracy.",
        "Mock outputs test pipeline mechanics only and must not be cited as model efficacy.",
    ]
    if dataset_kind == "synthetic-curated-smoke":
        limitations.append("This dataset is synthetic and illustrative; live holdout calibration remains open.")
    per_split = {}
    for split in SPLITS:
        summary = _classification_summary(
            split_matrices[split], split_labeled[split], split_evaluated[split]
        )
        per_split[split] = {
            "labeled": sum(split_labeled[split].values()),
            "evaluated": split_evaluated[split],
            "missing_or_error": sum(1 for item in missing if item["split"] == split),
            **summary,
        }
    return {
        "schema_version": 1,
        "dataset_kind": dataset_kind,
        "dataset_metadata": dataset_metadata or {},
        "limitations": limitations,
        "counts": {
            "labeled": len(cases),
            "evaluated": evaluated,
            "missing_or_error": len(missing),
            "coverage": _safe_ratio(evaluated, len(cases)),
        },
        **classification,
        "per_split": per_split,
        "false_pass": {"count": len(expensive), "denominator": false_pass_denominator, "rate": _safe_ratio(len(expensive), false_pass_denominator)},
        "review_burden": _safe_ratio(sum(matrix[actual]["review"] for actual in labels), evaluated),
        "redteam_regression": {
            "status": redteam_status,
            "hard_gate_passed": redteam_status == "passed",
            "labeled_support": len(redteam_cases),
            "evaluated_support": sum(
                case["id"] in valid_case_ids for case in redteam_cases
            ),
            "regressions": redteam_regressions,
        },
        "score_fit": {
            "labeled_count": expected_score_count,
            "evaluated_count": len(score_errors),
            "mae": statistics.fmean(score_errors) if score_errors else None,
            "normalized_mae": statistics.fmean(score_normalized) if score_normalized else None,
            "fit": 1.0 - statistics.fmean(score_normalized) if score_normalized else None,
            "nearest_level_fit": _safe_ratio(score_fit, len(score_errors)),
            "rounding": "floor(score + 0.5), clamped to the rubric score range",
            "missing": missing_scores,
        },
        "distribution": {
            "confidence_mean": statistics.fmean(confidences) if confidences else None,
            "confidence_median": statistics.median(confidences) if confidences else None,
            "confidence_floor_downgrades": floor_downgrades,
            "rule_fires": dict(rule_fires.most_common()),
            "unknown_rule_identity": unknown_rule_identity,
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
        "cost_usd": {
            source: {"total": sum(values.values()), "by_rubric": dict(values)}
            for source, values in sorted(costs.items())
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
    if replaced > 1:
        raise EvaluationError(
            f"ambiguous sweep target {answer}.{field}: {replaced} conditions match; "
            "sweep a target used by exactly one condition"
        )
    if field in ("noul", "confidence") and not 0 <= value <= 1:
        raise EvaluationError(f"{answer}.{field} sweep values must be between 0 and 1")
    if field == "score":
        maximum = _question_maximum(rubric, answer)
        if maximum is None or not 0 <= value <= maximum:
            raise EvaluationError(f"{answer}.score sweep values must be between 0 and {maximum}")
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


def _historical_gates(result: dict[str, Any], case_id: str) -> list[GateOutcome]:
    raw = result.get("deterministic_gates", [])
    if not isinstance(raw, list):
        raise EvaluationError(f"sweep case {case_id}: deterministic_gates must be a list")
    gates = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise EvaluationError(
                f"sweep case {case_id}: deterministic_gates[{index}] must be an object"
            )
        gate_id = item.get("gate_id")
        outcome = item.get("outcome")
        reason = item.get("reason")
        if not isinstance(gate_id, str) or not gate_id:
            raise EvaluationError(
                f"sweep case {case_id}: deterministic_gates[{index}].gate_id must be a non-empty string"
            )
        if outcome not in ("pass", "fail", "skip"):
            raise EvaluationError(
                f"sweep case {case_id}: deterministic_gates[{index}].outcome is invalid"
            )
        if not isinstance(reason, str):
            raise EvaluationError(
                f"sweep case {case_id}: deterministic_gates[{index}].reason must be a string"
            )
        gates.append(GateOutcome(gate_id, outcome, reason))
    return gates


def _trace_minimum_margin(trace: dict[str, Any]) -> float | None:
    matched_rule_id = trace.get("matched_rule_id")
    rules = trace.get("rules")
    if not isinstance(matched_rule_id, str) or not isinstance(rules, list):
        return None
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("rule_id") != matched_rule_id:
            continue
        margins = []
        for comparison in rule.get("comparisons", []):
            if not isinstance(comparison, dict) or comparison.get("evaluated") is not True:
                continue
            actual = comparison.get("actual")
            expected = comparison.get("expected")
            if (
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and isinstance(expected, (int, float))
                and not isinstance(expected, bool)
                and math.isfinite(actual)
                and math.isfinite(expected)
            ):
                margins.append(abs(float(actual) - float(expected)))
        return min(margins) if margins else None
    return None


def sweep(cases: list[dict[str, Any]], rows: dict[str, dict[str, Any]], specs: Iterable[str]) -> list[dict[str, Any]]:
    output = []
    for spec in specs:
        rubric_id, target, field, values = parse_sweep(spec)
        base = load_rubric(rubric_id)
        for value in values:
            rubric = copy.deepcopy(base)
            if field is None:
                if not 0 <= value <= 1:
                    raise EvaluationError("confidence_floor sweep values must be between 0 and 1")
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
                if row is None or result is None:
                    continue
                if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
                    row["result"] = None
                    row["error"] = "sweep requires a saved result with answer objects"
                    continue
                try:
                    rerouted = reroute_with_rubric(
                        rubric,
                        result["answers"],
                        historical_gates=_historical_gates(result, case["id"]),
                        reason_prefix="sweep",
                    )
                except (JudgeJevError, TypeError, ValueError) as err:
                    raise EvaluationError(
                        f"sweep case {case['id']}: saved answers cannot be rerouted: {err}"
                    ) from err
                routing = rerouted.routing
                trace = routing.trace_dict()
                trace["minimum_margin"] = _trace_minimum_margin(trace)
                result.update(
                    verdict=rerouted.verdict,
                    routing_reason=rerouted.reason,
                    stage=routing.stage,
                    deciding_answers=list(routing.deciding_answers),
                    confidence=routing.confidence,
                    confidence_floor=rubric.confidence_floor,
                    deterministic_gates=[gate.to_dict() for gate in rerouted.gates],
                    routing_trace=trace,
                )
            metrics = evaluate(cases, predicted_rows)
            output.append(
                {
                    "mode": "policy_only_reroute_of_saved_answers",
                    "spec": spec,
                    "rubric_id": rubric_id,
                    "target": target if field is None else f"{target}.{field}",
                    "value": value,
                    "counts": metrics["counts"],
                    "agreement": metrics["agreement"],
                    "macro_f1": metrics["macro_f1"],
                    "per_verdict": metrics["per_verdict"],
                    "per_split": metrics["per_split"],
                    "false_pass": metrics["false_pass"],
                    "review_burden": metrics["review_burden"],
                    "redteam_regression": metrics["redteam_regression"],
                    "distribution": metrics["distribution"],
                }
            )
    return output


def run_cases(cases: list[dict[str, Any]], judge: Path, *, live: bool) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        command = [str(judge.resolve()), "run", "--rubric", case["rubric_id"], "--input", "-"]
        if not live:
            command.append("--mock")
        completed = subprocess.run(
            command,
            input=json.dumps(case["input"], allow_nan=False),
            text=True,
            capture_output=True,
            check=False,
        )
        error = None
        try:
            result = (
                json.loads(completed.stdout, parse_constant=_reject_json_constant)
                if completed.stdout.strip()
                else None
            )
        except (json.JSONDecodeError, EvaluationError):
            result = None
        if not isinstance(result, dict):
            result = None
            error = completed.stderr.strip() or "judge returned no valid JSON object"
        else:
            verdict = result.get("verdict")
            expected_exit = EXIT_BY_VERDICT.get(verdict)
            if expected_exit is None or completed.returncode != expected_exit:
                result = None
                error = (
                    f"judge exit {completed.returncode} does not match JSON verdict {verdict!r}"
                )
        row = {"case_id": case["id"], "result": result, "provenance": "live" if live else "mock_pipeline"}
        if result is None:
            row["error"] = error or completed.stderr.strip() or f"judge exited {completed.returncode}"
        rows.append(row)
    return rows


def _unit_interval(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be a number from 0 to 1") from err
    if not math.isfinite(parsed) or not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be a finite number from 0 to 1")
    return parsed


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
    parser.add_argument("--ci-min-agreement", type=_unit_interval)
    parser.add_argument("--ci-min-macro-f1", type=_unit_interval)
    parser.add_argument("--ci-max-false-pass-rate", type=_unit_interval)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dataset_path = Path(args.dataset)
        cases = validate_cases(read_jsonl(dataset_path))
        dataset_metadata = read_dataset_metadata(dataset_path)
        if args.results:
            raw_results = read_jsonl(Path(args.results))
        else:
            raw_results = run_cases(cases, Path(args.judge), live=args.run_live)
        if args.write_results:
            Path(args.write_results).write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                    for row in raw_results
                ),
                encoding="utf-8",
            )
        indexed = index_results(raw_results)
        report = evaluate(cases, indexed, dataset_metadata)
        if args.sweep:
            report["sweeps"] = sweep(cases, indexed, args.sweep)
        text = json.dumps(
            report, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False
        ) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
        print(text, end="")
        failed = (
            report["counts"]["missing_or_error"] > 0
            or report["counts"]["evaluated"] != report["counts"]["labeled"]
            or report["score_fit"]["evaluated_count"] != report["score_fit"]["labeled_count"]
            or not report["redteam_regression"]["hard_gate_passed"]
        )
        if args.ci_min_agreement is not None and report["agreement"] < args.ci_min_agreement:
            failed = True
        if args.ci_min_macro_f1 is not None and report["macro_f1"] < args.ci_min_macro_f1:
            failed = True
        if args.ci_max_false_pass_rate is not None and report["false_pass"]["rate"] > args.ci_max_false_pass_rate:
            failed = True
        return 2 if failed else 0
    except (EvaluationError, JudgeJevError, OSError, subprocess.SubprocessError) as err:
        print(f"evaluate: {err}", file=sys.stderr)
        return 11


if __name__ == "__main__":
    raise SystemExit(main())

"""Proposal-only numeric policy search over recorded answers.

The update is a seeded group-relative, CEM-like policy search. It borrows the
within-group advantage ``(reward - mean) / std``; it is not language-model RL.
"""

from __future__ import annotations

import copy
import difflib
import json
import math
import random
import statistics
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from judge_jev.evaluation import evaluate as evaluate_labeled
from judge_jev.evaluation import index_results, validate_cases
from judge_jev.models import VERDICTS, GateOutcome, JudgeJevError, Rubric
from judge_jev.paths import rubrics_dir, tuning_dir
from judge_jev.reroute import reroute_with_rubric
from judge_jev.rubric import load_rubric, rubric_content_hash

SPLITS = ("train", "heldout", "frozen-redteam")


@dataclass(frozen=True)
class NumericParameter:
    name: str
    low: float
    high: float
    initial: float
    rule_index: int | None = None
    condition_index: int | None = None
    floor_stakes: str | None = None


@dataclass
class Candidate:
    values: dict[str, float]
    metrics: dict[str, Any]
    reward: float
    redteam_passed: bool


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    paths = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    rows: list[dict[str, Any]] = []
    for source in paths:
        for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as err:
                raise JudgeJevError(f"{source}:{line_no}: invalid JSON: {err}") from err
            if not isinstance(value, dict):
                raise JudgeJevError(f"{source}:{line_no}: row must be an object")
            rows.append(value)
    return rows


def load_cost_policy(path: Path | None = None) -> dict[str, Any]:
    source = path or tuning_dir() / "cost-policy-v1.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("costs"), dict):
        raise JudgeJevError("cost policy needs a costs mapping")
    required = data.get("required_classes")
    if not isinstance(required, list) or not required or any(item not in VERDICTS for item in required):
        raise JudgeJevError("cost policy required_classes must be a non-empty list of verdicts")
    for name, value in data["costs"].items():
        if name != "default":
            parts = name.removeprefix("actual_").split("_predicted_")
            if len(parts) != 2 or any(part not in VERDICTS for part in parts):
                raise JudgeJevError(f"cost policy has invalid cell {name!r}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise JudgeJevError(f"cost policy {name} must be finite and non-negative")
    for name in ("review_cost", "token_cost_per_1000"):
        value = data.get(name, 0.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise JudgeJevError(f"cost policy {name} must be finite and non-negative")
    return data


def join_corpus(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    rubric_id: str | None = None,
    allow_legacy_provenance: bool = False,
) -> list[dict[str, Any]]:
    target_rubric = load_rubric(rubric_id) if rubric_id is not None else None
    target_hash = rubric_content_hash(target_rubric) if target_rubric is not None else None
    indexed: dict[str, dict[str, Any]] = {}
    for row in results:
        case_id = row.get("case_id") or row.get("id")
        if not isinstance(case_id, str) or case_id in indexed:
            raise JudgeJevError("result rows need unique case_id")
        indexed[case_id] = row
    joined = []
    case_ids = set()
    for case in cases:
        case_id = case.get("id")
        split = case.get("split")
        expected = case.get("expected_verdict")
        if not isinstance(case_id, str) or case_id in case_ids:
            raise JudgeJevError("cases need unique string id")
        if split not in SPLITS or expected not in VERDICTS:
            raise JudgeJevError(f"case {case_id!r} has invalid split or expected_verdict")
        if rubric_id is not None and case.get("rubric_id") != rubric_id:
            raise JudgeJevError(
                f"case {case_id!r} targets rubric {case.get('rubric_id')!r}, expected {rubric_id!r}"
            )
        case_ids.add(case_id)
        result_row = indexed.get(case_id)
        if result_row is None:
            raise JudgeJevError(f"case {case_id!r} has no result; incomplete coverage is not measurable")
        result = result_row.get("result", result_row)
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise JudgeJevError(f"case {case_id!r} result has no answers")
        if target_rubric is not None:
            if result.get("rubric_id") != target_rubric.id:
                raise JudgeJevError(
                    f"case {case_id!r} result rubric_id {result.get('rubric_id')!r} "
                    f"does not match {target_rubric.id!r}"
                )
            if result.get("rubric_version") != target_rubric.version:
                raise JudgeJevError(
                    f"case {case_id!r} result rubric_version {result.get('rubric_version')!r} "
                    f"does not match {target_rubric.version!r}"
                )
            saved_hash = result.get("rubric_hash")
            if saved_hash is None and not allow_legacy_provenance:
                raise JudgeJevError(
                    f"case {case_id!r} result has no rubric_hash; legacy evidence cannot "
                    "support a real-quality tuning proposal"
                )
            if saved_hash is not None and saved_hash != target_hash:
                raise JudgeJevError(
                    f"case {case_id!r} result rubric_hash does not match the effective rubric"
                )
        joined.append({**case, "result": result, "result_row": result_row})
    extras = set(indexed) - case_ids
    if extras:
        raise JudgeJevError(f"results contain unknown case ids: {sorted(extras)}")
    return joined


def validate_support(
    rows: list[dict[str, Any]],
    required_classes: Iterable[str],
    minimum: int,
) -> dict[str, dict[str, int]]:
    labels = list(required_classes)
    if minimum < 1:
        raise JudgeJevError("minimum support must be at least 1")
    if not labels or any(label not in VERDICTS for label in labels):
        raise JudgeJevError("required classes must be a non-empty list of verdicts")
    support = {split: {label: 0 for label in labels} for split in SPLITS}
    for row in rows:
        if row["expected_verdict"] in support[row["split"]]:
            support[row["split"]][row["expected_verdict"]] += 1
    missing = [
        f"{split}:{label}={count}"
        for split, labels in support.items()
        for label, count in labels.items()
        if count < minimum
    ]
    if missing:
        raise JudgeJevError(
            f"insufficient class/split support (minimum {minimum}): {', '.join(missing)}"
        )
    return support


def discover_parameters(rubric: Rubric) -> list[NumericParameter]:
    parameters: list[NumericParameter] = []
    for rule_index, rule in enumerate(rubric.rules):
        for condition_index, condition in enumerate(rule.conditions):
            if condition.field == "choice":
                continue
            question = rubric.questions[condition.answer]
            high = 1.0
            if condition.field == "score":
                high = float(len(question.get("criteria") or []) - 1)
            initial = float(condition.value)
            parameters.append(
                NumericParameter(
                    f"routing.rules.{rule_index}.all.{condition_index}.value",
                    0.0,
                    high,
                    initial,
                    rule_index=rule_index,
                    condition_index=condition_index,
                )
            )
    parameters.append(
        NumericParameter(
            f"confidence_floors.{rubric.stakes}",
            0.0,
            1.0,
            rubric.confidence_floor,
            floor_stakes=rubric.stakes,
        )
    )
    return parameters


def apply_candidate(rubric: Rubric, parameters: list[NumericParameter], values: dict[str, float]) -> Rubric:
    rules = list(rubric.rules)
    floors = dict(rubric.confidence_floors)
    for parameter in parameters:
        value = min(parameter.high, max(parameter.low, float(values[parameter.name])))
        if parameter.floor_stakes is not None:
            floors[parameter.floor_stakes] = value
            continue
        assert parameter.rule_index is not None and parameter.condition_index is not None
        rule = rules[parameter.rule_index]
        conditions = list(rule.conditions)
        conditions[parameter.condition_index] = replace(conditions[parameter.condition_index], value=value)
        rules[parameter.rule_index] = replace(rule, conditions=tuple(conditions))
    return replace(rubric, confidence_floors=floors, rules=tuple(rules))


def default_reroute(rubric: Rubric, result: dict[str, Any]) -> str:
    gates = [
        GateOutcome(str(gate["gate_id"]), str(gate["outcome"]), str(gate["reason"]))
        for gate in result.get("deterministic_gates") or []
    ]
    return reroute_with_rubric(rubric, result["answers"], historical_gates=gates).verdict


def evaluate_candidate(
    rubric: Rubric,
    rows: list[dict[str, Any]],
    cost_policy: dict[str, Any],
    *,
    reroute: Callable[[Rubric, dict[str, Any]], str] = default_reroute,
) -> dict[str, Any]:
    costs = cost_policy["costs"]
    default_cost = float(costs.get("default", 0.0))
    split_reports: dict[str, Any] = {}
    total_cost = 0.0
    total_tokens = 0
    all_usage_known = True
    predictions: dict[str, str] = {}
    for split in SPLITS:
        subset = [row for row in rows if row["split"] == split]
        matrix = {actual: {predicted: 0 for predicted in VERDICTS} for actual in VERDICTS}
        split_cost = 0.0
        review = 0
        safety_total = 0
        safety_kept = 0
        pass_predictions = 0
        true_pass = 0
        split_tokens = 0
        usage_known = True
        for row in subset:
            predicted = reroute(rubric, row["result"])
            actual = row["expected_verdict"]
            predictions[row["id"]] = predicted
            matrix[actual][predicted] += 1
            cell = f"actual_{actual}_predicted_{predicted}"
            split_cost += float(costs.get(cell, 0.0 if actual == predicted else default_cost))
            if predicted in ("review", "escalate"):
                review += 1
            if actual in ("fail", "escalate"):
                safety_total += 1
                safety_kept += int(predicted != "pass")
            if predicted == "pass":
                pass_predictions += 1
                true_pass += int(actual == "pass")
            usage = row["result"].get("usage") or {}
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if type(input_tokens) is not int or type(output_tokens) is not int:
                usage_known = False
            else:
                split_tokens += input_tokens + output_tokens
        count = len(subset)
        total_cost += split_cost
        all_usage_known = all_usage_known and usage_known
        if usage_known:
            total_tokens += split_tokens
        split_reports[split] = {
            "count": count,
            "confusion_matrix": matrix,
            "expected_confusion_cost": split_cost / count if count else None,
            "safety_recall": safety_kept / safety_total if safety_total else None,
            "safety_support": safety_total,
            "pass_precision": true_pass / pass_predictions if pass_predictions else None,
            "pass_prediction_support": pass_predictions,
            "human_review_volume": review / count if count else None,
            "review_count": review,
            "tokens": split_tokens if usage_known else None,
            "token_support": count if usage_known else 0,
        }
    train = split_reports["train"]
    train_count = train["count"] or 1
    token_cost = None
    if train["tokens"] is not None:
        token_cost = (
            float(cost_policy.get("token_cost_per_1000", 0.0))
            * float(train["tokens"])
            / (1000 * train_count)
        )
    objective_cost = (
        float(train["expected_confusion_cost"] or 0.0)
        + float(cost_policy.get("review_cost", 0.0)) * float(train["human_review_volume"] or 0.0)
        + (token_cost or 0.0)
    )
    return {
        "splits": split_reports,
        "negative_expected_cost_reward": -objective_cost,
        "total_confusion_cost": total_cost,
        "token_cost": token_cost,
        "total_tokens": total_tokens if all_usage_known else None,
        "usage_complete": all_usage_known,
        "predictions": predictions,
    }


def canonical_report(
    rubric: Rubric,
    rows: list[dict[str, Any]],
    reroute: Callable[[Rubric, dict[str, Any]], str],
) -> dict[str, Any]:
    cases = [
        {key: value for key, value in row.items() if key not in {"result", "result_row"}}
        for row in rows
    ]
    result_rows = []
    for row in rows:
        result_row = copy.deepcopy(row["result_row"])
        result = copy.deepcopy(row["result"])
        result["source_rubric_version"] = result.get("rubric_version")
        result["source_rubric_hash"] = result.get("rubric_hash")
        result["rubric_id"] = rubric.id
        result["rubric_version"] = rubric.version
        # A candidate is a distinct effective policy even before its proposed
        # version is committed. Never carry the source policy's trace forward.
        result.pop("routing_trace", None)
        if reroute is default_reroute:
            historical_gates = [
                GateOutcome(str(gate["gate_id"]), str(gate["outcome"]), str(gate["reason"]))
                for gate in result.get("deterministic_gates") or []
            ]
            routed = reroute_with_rubric(
                rubric, result["answers"], historical_gates=historical_gates
            )
            result.update(
                {
                    "verdict": routed.verdict,
                    "routing_reason": routed.reason,
                    "stage": routed.routing.stage,
                    "deciding_answers": list(routed.routing.deciding_answers),
                    "confidence": routed.routing.confidence,
                    "confidence_floor": rubric.confidence_floor,
                    "deterministic_gates": [gate.to_dict() for gate in routed.gates],
                    "routing_trace": routed.routing.trace_dict(),
                    "rubric_hash": rubric_content_hash(rubric),
                }
            )
        else:
            # Custom rerouters are a test/demo seam and cannot supply a canonical
            # routing trace. Leaving the hash absent keeps that limitation visible.
            result["verdict"] = reroute(rubric, row["result"])
            result.pop("rubric_hash", None)
        result_row["result"] = result
        result_rows.append(result_row)
    return evaluate_labeled(cases, index_results(result_rows))


def group_advantages(rewards: list[float]) -> list[float]:
    if not rewards:
        return []
    mean = statistics.fmean(rewards)
    variance = statistics.fmean((value - mean) ** 2 for value in rewards)
    if variance <= 1e-18:
        return [0.0 for _ in rewards]
    std = math.sqrt(variance)
    return [(value - mean) / std for value in rewards]


def pareto_front(candidates: list[Candidate]) -> list[Candidate]:
    def objectives(candidate: Candidate) -> tuple[float, float, float, float] | None:
        held = candidate.metrics["splits"]["heldout"]
        recall = held["safety_recall"]
        precision = held["pass_precision"]
        review = held["human_review_volume"]
        tokens = held["tokens"]
        if recall is None or precision is None or review is None or tokens is None:
            return None
        return (float(recall), float(precision), -float(review), -tokens)

    frontier = []
    eligible = [candidate for candidate in candidates if candidate.redteam_passed]
    for candidate in eligible:
        current = objectives(candidate)
        if current is None:
            continue
        dominated = any(
            (other_objectives := objectives(other)) is not None
            and all(a >= b for a, b in zip(other_objectives, current))
            and any(a > b for a, b in zip(other_objectives, current))
            for other in eligible
            if other is not candidate
        )
        if not dominated:
            frontier.append(candidate)
    return frontier


def _proposed_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return f"{parts[0]}.{int(parts[1]) + 1}.0"
    return version + "+proposal"


def candidate_diff(rubric_path: Path, parameters: list[NumericParameter], candidate: Candidate) -> str:
    original = rubric_path.read_text(encoding="utf-8")
    data = yaml.safe_load(original)
    data["version"] = _proposed_version(str(data["version"]))
    for parameter in parameters:
        value = candidate.values[parameter.name]
        if parameter.floor_stakes:
            data["confidence_floors"][parameter.floor_stakes] = value
        else:
            data["routing"]["rules"][parameter.rule_index]["all"][parameter.condition_index]["value"] = value
    proposed = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    # Parse the proposal before presenting it. The source file is never written.
    if not isinstance(yaml.safe_load(proposed), dict):
        raise JudgeJevError("generated proposal does not load as YAML")
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=str(rubric_path),
            tofile=str(rubric_path) + ".proposed",
        )
    )


def threshold_search(
    rubric_id: str,
    cases_path: Path,
    results_path: Path,
    *,
    seed: int = 1,
    iterations: int = 8,
    candidates_per_iteration: int = 24,
    minimum_support: int = 2,
    synthetic_demo: bool = False,
    cost_policy_path: Path | None = None,
    reroute: Callable[[Rubric, dict[str, Any]], str] = default_reroute,
) -> dict[str, Any]:
    if not 1 <= iterations <= 100 or not 2 <= candidates_per_iteration <= 256:
        raise JudgeJevError("iterations must be 1..100 and candidates must be 2..256")
    if minimum_support < 1:
        raise JudgeJevError("minimum support must be at least 1")
    policy = load_cost_policy(cost_policy_path)
    try:
        cases = validate_cases(read_jsonl(cases_path))
        raw_results = read_jsonl(results_path)
        index_results(raw_results)
    except (ValueError, OSError) as err:
        raise JudgeJevError(str(err)) from err
    rows = join_corpus(
        cases,
        raw_results,
        rubric_id=rubric_id,
        allow_legacy_provenance=synthetic_demo,
    )
    support_warning = None
    try:
        support = validate_support(rows, policy.get("required_classes", []), minimum_support)
    except JudgeJevError as err:
        if not synthetic_demo:
            raise
        support = {split: {} for split in SPLITS}
        support_warning = str(err)
    rubric = load_rubric(rubric_id)
    parameters = discover_parameters(rubric)
    rng = random.Random(seed)
    means = {parameter.name: parameter.initial for parameter in parameters}
    stds = {parameter.name: max((parameter.high - parameter.low) / 6.0, 1e-6) for parameter in parameters}
    train_rows = [row for row in rows if row["split"] == "train"]
    baseline_train = evaluate_candidate(rubric, train_rows, policy, reroute=reroute)
    if float(policy.get("token_cost_per_1000", 0.0)) > 0 and not baseline_train["usage_complete"]:
        raise JudgeJevError(
            "training results have unknown token usage; the declared token-cost term cannot be evaluated"
        )
    baseline_candidate = Candidate(
            dict(means),
            baseline_train,
            baseline_train["negative_expected_cost_reward"],
            True,
        )
    all_candidates: list[Candidate] = [baseline_candidate]
    iteration_reports = []
    for iteration in range(iterations):
        group: list[Candidate] = []
        for _ in range(candidates_per_iteration):
            values = {
                parameter.name: min(
                    parameter.high,
                    max(parameter.low, rng.gauss(means[parameter.name], stds[parameter.name])),
                )
                for parameter in parameters
            }
            candidate_rubric = apply_candidate(rubric, parameters, values)
            # The adaptive distribution sees training rows only. Held-out and
            # frozen-red-team labels are not consulted until search has stopped.
            metrics = evaluate_candidate(candidate_rubric, train_rows, policy, reroute=reroute)
            reward = metrics["negative_expected_cost_reward"]
            group.append(Candidate(values, metrics, reward, True))
        rewards = [candidate.reward for candidate in group]
        advantages = group_advantages(rewards)
        weights = [math.exp(max(-20.0, min(20.0, advantage))) for advantage in advantages]
        weight_sum = sum(weights)
        if weight_sum > 0 and any(advantage != 0 for advantage in advantages):
            for parameter in parameters:
                new_mean = sum(weight * candidate.values[parameter.name] for weight, candidate in zip(weights, group)) / weight_sum
                variance = sum(
                    weight * (candidate.values[parameter.name] - new_mean) ** 2
                    for weight, candidate in zip(weights, group)
                ) / weight_sum
                means[parameter.name] = min(parameter.high, max(parameter.low, new_mean))
                stds[parameter.name] = max(math.sqrt(variance), (parameter.high - parameter.low) * 0.005)
        all_candidates.extend(group)
        iteration_reports.append(
            {
                "iteration": iteration,
                "rewards": rewards,
                "advantages": advantages,
                "distribution": {name: {"mean": means[name], "std": stds[name]} for name in means},
            }
        )
    # Post-search checks are intentionally bounded and cannot affect the learned
    # distribution. They expose held-out tradeoffs and enforce the evaluator's
    # exact per-case frozen-red-team baseline gate.
    shortlisted = sorted(all_candidates, key=lambda item: item.reward, reverse=True)[:32]
    if baseline_candidate not in shortlisted:
        shortlisted[-1] = baseline_candidate
    evaluated_candidates: list[Candidate] = []
    for candidate in shortlisted:
        candidate_rubric = apply_candidate(rubric, parameters, candidate.values)
        full_metrics = evaluate_candidate(candidate_rubric, rows, policy, reroute=reroute)
        canonical = canonical_report(candidate_rubric, rows, reroute)
        full_metrics["evaluation"] = canonical
        complete = canonical["counts"]["evaluated"] == canonical["counts"]["labeled"]
        redteam_passed = complete and canonical["redteam_regression"]["hard_gate_passed"]
        evaluated_candidates.append(
            Candidate(candidate.values, full_metrics, candidate.reward, redteam_passed)
        )
    frontier = pareto_front(evaluated_candidates)
    rubric_path = rubrics_dir() / f"{rubric_id}.yaml"
    proposals = [
        {
            "values": candidate.values,
            "reward": candidate.reward,
            "redteam_passed": candidate.redteam_passed,
            "metrics": candidate.metrics,
            "diff": candidate_diff(rubric_path, parameters, candidate),
        }
        for candidate in frontier
    ]
    return {
        "version": 1,
        "algorithm": "seeded group-relative CEM-like policy search (not language-model RL)",
        "proposal_only": True,
        "api_calls": 0,
        "synthetic_demo": synthetic_demo,
        "quality_claim_allowed": (
            not synthetic_demo and support_warning is None and bool(proposals)
        ),
        "support": support,
        "support_warning": support_warning,
        "seed": seed,
        "iterations": iterations,
        "candidates_per_iteration": candidates_per_iteration,
        "parameters": [asdict(parameter) for parameter in parameters],
        "cost_policy": policy,
        "search_fit_split": "train",
        "heldout_and_redteam_used_during_adaptation": False,
        "baseline": next(
            (
                candidate.metrics
                for candidate in evaluated_candidates
                if candidate.values == {parameter.name: parameter.initial for parameter in parameters}
            ),
            baseline_train,
        ),
        "iterations_report": iteration_reports,
        "postsearch_candidates_evaluated": len(evaluated_candidates),
        "pareto_frontier": proposals,
    }

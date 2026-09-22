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
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from judge_jev.models import Condition, JudgeJevError, RoutingRule, Rubric, VERDICTS
from judge_jev.paths import repo_root
from judge_jev.routing import route_verdict
from judge_jev.rubric import load_rubric

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
    source = path or repo_root() / "shared" / "tuning" / "cost-policy-v1.yaml"
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("costs"), dict):
        raise JudgeJevError("cost policy needs a costs mapping")
    return data


def join_corpus(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        case_ids.add(case_id)
        result_row = indexed.get(case_id)
        if result_row is None:
            raise JudgeJevError(f"case {case_id!r} has no result; incomplete coverage is not measurable")
        result = result_row.get("result", result_row)
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise JudgeJevError(f"case {case_id!r} result has no answers")
        joined.append({**case, "result": result})
    extras = set(indexed) - case_ids
    if extras:
        raise JudgeJevError(f"results contain unknown case ids: {sorted(extras)}")
    return joined


def validate_support(
    rows: list[dict[str, Any]],
    required_classes: Iterable[str],
    minimum: int,
) -> dict[str, dict[str, int]]:
    support = {split: {label: 0 for label in required_classes} for split in SPLITS}
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


def _historical_gate_override(result: dict[str, Any], verdict: str) -> str:
    for gate in result.get("deterministic_gates") or []:
        if gate.get("gate_id") == "injection_heuristic" and gate.get("outcome") == "fail":
            return "escalate"
    return verdict


def default_reroute(rubric: Rubric, result: dict[str, Any]) -> str:
    verdict = route_verdict(rubric, result["answers"])[0]
    return _historical_gate_override(result, verdict)


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
            split_tokens += int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        count = len(subset)
        total_cost += split_cost
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
            "tokens": split_tokens,
        }
    train = split_reports["train"]
    train_count = train["count"] or 1
    objective_cost = (
        float(train["expected_confusion_cost"] or 0.0)
        + float(cost_policy.get("review_cost", 0.0)) * float(train["human_review_volume"] or 0.0)
        + float(cost_policy.get("token_cost_per_1000", 0.0)) * float(train["tokens"]) / (1000 * train_count)
    )
    return {
        "splits": split_reports,
        "negative_expected_cost_reward": -objective_cost,
        "total_confusion_cost": total_cost,
        "total_tokens": total_tokens,
        "predictions": predictions,
    }


def _redteam_non_regression(baseline: dict[str, Any], candidate: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    for row in rows:
        if row["split"] != "frozen-redteam":
            continue
        case_id = row["id"]
        before = baseline["predictions"][case_id]
        after = candidate["predictions"][case_id]
        if before != "pass" and after == "pass":
            return False
    return True


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
    def objectives(candidate: Candidate) -> tuple[float, float, float, float]:
        held = candidate.metrics["splits"]["heldout"]
        recall = held["safety_recall"] if held["safety_recall"] is not None else 0.0
        precision = held["pass_precision"] if held["pass_precision"] is not None else 0.0
        review = held["human_review_volume"] if held["human_review_volume"] is not None else 1.0
        tokens = float(held["tokens"])
        return (float(recall), float(precision), -float(review), -tokens)

    frontier = []
    eligible = [candidate for candidate in candidates if candidate.redteam_passed]
    for candidate in eligible:
        current = objectives(candidate)
        dominated = any(
            all(a >= b for a, b in zip(objectives(other), current))
            and any(a > b for a, b in zip(objectives(other), current))
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
    policy = load_cost_policy(cost_policy_path)
    rows = join_corpus(read_jsonl(cases_path), read_jsonl(results_path))
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
    baseline_metrics = evaluate_candidate(rubric, rows, policy, reroute=reroute)
    all_candidates: list[Candidate] = [
        Candidate(dict(means), baseline_metrics, baseline_metrics["negative_expected_cost_reward"], True)
    ]
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
            metrics = evaluate_candidate(candidate_rubric, rows, policy, reroute=reroute)
            redteam = _redteam_non_regression(baseline_metrics, metrics, rows)
            reward = metrics["negative_expected_cost_reward"] if redteam else float("-inf")
            group.append(Candidate(values, metrics, reward, redteam))
        finite_rewards = [candidate.reward if math.isfinite(candidate.reward) else -1e12 for candidate in group]
        advantages = group_advantages(finite_rewards)
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
                "rewards": finite_rewards,
                "advantages": advantages,
                "distribution": {name: {"mean": means[name], "std": stds[name]} for name in means},
            }
        )
    frontier = pareto_front(all_candidates)
    rubric_path = repo_root() / "shared" / "rubrics" / f"{rubric_id}.yaml"
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
        "quality_claim_allowed": not synthetic_demo and support_warning is None,
        "support": support,
        "support_warning": support_warning,
        "seed": seed,
        "iterations": iterations,
        "candidates_per_iteration": candidates_per_iteration,
        "parameters": [asdict(parameter) for parameter in parameters],
        "cost_policy": policy,
        "baseline": baseline_metrics,
        "iterations_report": iteration_reports,
        "pareto_frontier": proposals,
    }

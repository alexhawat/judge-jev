"""Route verdicts from Jev answers in code.

Rules are declarative data, shared verbatim with the Rust runtime. Nothing here
evaluates a string as code.
"""

from __future__ import annotations

import operator
from dataclasses import asdict, dataclass
from typing import Any, Callable

from judge_jev.models import (
    GATED_VERDICTS,
    STAGE_ORDER,
    Condition,
    Rubric,
)

# Comparison operators a condition may use.
OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}

# Which fields each answer type exposes to a condition.
FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "noul": frozenset({"noul"}),
    "choice": frozenset({"choice", "confidence"}),
    "score": frozenset({"score", "confidence"}),
}

# Fields compared as text rather than numbers.
TEXT_FIELDS = frozenset({"choice"})


class UnevaluableRule(Exception):
    """A rule could not be evaluated because an answer it reads is absent.

    This is never treated as "the rule did not match": a missing answer means the
    funnel cannot establish what the rule was there to establish, so the judgment
    escalates instead of falling through to a laxer rule.
    """

    def __init__(self, rule_index: int, answer_id: str) -> None:
        super().__init__(f"rule {rule_index} reads missing answer '{answer_id}'")
        self.rule_index = rule_index
        self.answer_id = answer_id


@dataclass(frozen=True)
class ComparisonTrace:
    answer: str
    field: str
    op: str
    expected: Any
    actual: Any = None
    evaluated: bool = False
    matched: bool | None = None


@dataclass(frozen=True)
class RuleTrace:
    rule_id: str
    verdict: str
    default: bool
    outcome: str
    comparisons: tuple[ComparisonTrace, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoutingEvaluation:
    verdict: str
    reason: str
    stage: str
    deciding_answers: tuple[str, ...]
    confidence: float
    confidence_candidate: str | None
    matched_rule_id: str | None
    rules: tuple[RuleTrace, ...]

    def public_tuple(self) -> tuple[str, str, str, list[str], float]:
        return self.verdict, self.reason, self.stage, list(self.deciding_answers), self.confidence

    def candidate_tuple(self) -> tuple[str, str, str, list[str], float, str | None]:
        return (*self.public_tuple(), self.confidence_candidate)

    def trace_dict(self) -> dict[str, Any]:
        return {
            "matched_rule_id": self.matched_rule_id,
            "confidence_candidate": self.confidence_candidate,
            "rules": [rule.to_dict() for rule in self.rules],
        }


def answer_confidence(answer: dict[str, Any]) -> float | None:
    """How certain this answer is, on 0-1.

    Choice and Score carry `confidence` directly. Noul has none, so the distance
    from 0.5 stands in for it: 0.5 is a coin flip, 0 and 1 are certain.
    """
    kind = answer.get("type")
    if kind == "noul" and "noul" in answer:
        return abs(float(answer["noul"]) - 0.5) * 2.0
    if kind in ("choice", "score") and "confidence" in answer:
        return float(answer["confidence"])
    return None


def _evaluate(condition: Condition, answer: dict[str, Any]) -> bool:
    if condition.field not in answer:
        # The rubric validated against the question's type, so a field missing here
        # means the response shape differs from the declared type.
        raise UnevaluableRule(-1, condition.answer)
    left = answer[condition.field]
    right = condition.value
    if condition.field in TEXT_FIELDS:
        return OPS[condition.op](str(left), str(right))
    return OPS[condition.op](float(left), float(right))


def _stage_for(rubric: Rubric, answer_ids: tuple[str, ...]) -> str:
    """The earliest funnel stage among the questions a rule reads."""
    stages = [s for s in (rubric.stage_of(a) for a in answer_ids) if s in STAGE_ORDER]
    if not stages:
        return "route"
    return min(stages, key=STAGE_ORDER.index)


def decision_confidence(
    answers: dict[str, dict[str, Any]], deciding: tuple[str, ...]
) -> float:
    """Confidence in the verdict: the least certain answer that produced it.

    Only the answers the matched rule actually read count. Taking the minimum means
    one uncertain input is enough to hold the whole verdict back, which is the point
    of a floor.
    """
    values = [
        c
        for c in (answer_confidence(answers[a]) for a in deciding if a in answers)
        if c is not None
    ]
    if not values:
        return 0.0
    return min(values)


def route_verdict(
    rubric: Rubric, answers: dict[str, dict[str, Any]]
) -> tuple[str, str, str, list[str], float]:
    """Return the stable public routing tuple."""
    return evaluate_routing(rubric, answers).public_tuple()


def route_verdict_with_candidate(
    rubric: Rubric, answers: dict[str, dict[str, Any]]
) -> tuple[str, str, str, list[str], float, str | None]:
    """Return routing fields plus any pass/fail candidate downgraded by the floor."""
    return evaluate_routing(rubric, answers).candidate_tuple()


def evaluate_routing(rubric: Rubric, answers: dict[str, dict[str, Any]]) -> RoutingEvaluation:
    """Evaluate ordered rules once and return the decision plus its exact trace.

    The first rule whose conditions all hold wins. A rule that cannot be evaluated
    escalates rather than being skipped. Stable rule IDs are their zero-padded
    position in the versioned rubric, so explain/evaluation/tuning share identity.
    """
    missing = [answer_id for answer_id in rubric.questions if answer_id not in answers]
    if missing:
        return RoutingEvaluation(
            verdict="escalate",
            reason="Judgment response is incomplete; missing answers: "
            + ", ".join(sorted(missing))
            + ".",
            stage=_stage_for(rubric, tuple(missing)),
            deciding_answers=(),
            confidence=0.0,
            confidence_candidate=None,
            matched_rule_id=None,
            rules=tuple(
                RuleTrace(f"rule:{index:03d}", rule.verdict, rule.default, "not_evaluated")
                for index, rule in enumerate(rubric.rules)
            ),
        )

    traces: list[RuleTrace] = []
    for index, rule in enumerate(rubric.rules):
        rule_id = f"rule:{index:03d}"
        if rule.default:
            # The catch-all decided nothing, so there is no confidence to report.
            if rule.verdict in GATED_VERDICTS and 0.0 < rubric.confidence_floor:
                traces.append(RuleTrace(rule_id, rule.verdict, True, "matched"))
                traces.extend(
                    RuleTrace(f"rule:{later:03d}", item.verdict, item.default, "not_evaluated")
                    for later, item in enumerate(rubric.rules[index + 1 :], index + 1)
                )
                return RoutingEvaluation(
                    verdict="review",
                    reason=(
                        f"{rule.reason} Downgraded from '{rule.verdict}': confidence "
                        f"0.00 is below the {rubric.stakes} floor of "
                        f"{rubric.confidence_floor:.2f}."
                    ),
                    stage="route",
                    deciding_answers=(),
                    confidence=0.0,
                    confidence_candidate=rule.verdict,
                    matched_rule_id=rule_id,
                    rules=tuple(traces),
                )
            traces.append(RuleTrace(rule_id, rule.verdict, True, "matched"))
            return RoutingEvaluation(
                rule.verdict, rule.reason, "route", (), 0.0, None, rule_id, tuple(traces)
            )

        comparisons: list[ComparisonTrace] = []
        matched = True
        for condition_index, condition in enumerate(rule.conditions):
            answer = answers.get(condition.answer)
            if answer is None or condition.field not in answer:
                comparisons.append(
                    ComparisonTrace(condition.answer, condition.field, condition.op, condition.value)
                )
                comparisons.extend(
                    ComparisonTrace(c.answer, c.field, c.op, c.value)
                    for c in rule.conditions[condition_index + 1 :]
                )
                traces.append(
                    RuleTrace(rule_id, rule.verdict, False, "unevaluable", tuple(comparisons))
                )
                return RoutingEvaluation(
                    "escalate",
                    f"Could not evaluate '{rule.verdict}' rule: answer '{condition.answer}' is missing.",
                    _stage_for(rubric, rule.answer_ids),
                    (),
                    0.0,
                    None,
                    rule_id,
                    tuple(traces),
                )
            holds = _evaluate(condition, answer)
            comparisons.append(
                ComparisonTrace(
                    condition.answer,
                    condition.field,
                    condition.op,
                    condition.value,
                    answer[condition.field],
                    True,
                    holds,
                )
            )
            if not holds:
                comparisons.extend(
                    ComparisonTrace(c.answer, c.field, c.op, c.value)
                    for c in rule.conditions[condition_index + 1 :]
                )
                matched = False
                break

        if not matched:
            traces.append(RuleTrace(rule_id, rule.verdict, False, "not_matched", tuple(comparisons)))
            continue

        traces.append(RuleTrace(rule_id, rule.verdict, False, "matched", tuple(comparisons)))
        deciding = rule.answer_ids
        confidence = decision_confidence(answers, deciding)
        stage = _stage_for(rubric, deciding)
        floor = rubric.confidence_floor

        if rule.verdict in GATED_VERDICTS and confidence < floor:
            # The rule matched, but not confidently enough to act on automatically.
            traces.extend(
                RuleTrace(f"rule:{later:03d}", item.verdict, item.default, "not_evaluated")
                for later, item in enumerate(rubric.rules[index + 1 :], index + 1)
            )
            return RoutingEvaluation(
                verdict="review",
                reason=(
                    f"{rule.reason} Downgraded from '{rule.verdict}': confidence "
                    f"{confidence:.2f} is below the {rubric.stakes} floor of {floor:.2f}."
                ),
                stage=stage,
                deciding_answers=deciding,
                confidence=confidence,
                confidence_candidate=rule.verdict,
                matched_rule_id=rule_id,
                rules=tuple(traces),
            )

        traces.extend(
            RuleTrace(f"rule:{later:03d}", item.verdict, item.default, "not_evaluated")
            for later, item in enumerate(rubric.rules[index + 1 :], index + 1)
        )
        return RoutingEvaluation(
            rule.verdict,
            rule.reason,
            stage,
            deciding,
            confidence,
            None,
            rule_id,
            tuple(traces),
        )

    # No rule matched and the rubric declared no default.
    return RoutingEvaluation(
        "review", "No routing rule matched.", "route", (), 0.0, None, None, tuple(traces)
    )


def _raise_missing(index: int, answer_id: str) -> bool:
    raise UnevaluableRule(index, answer_id)

"""Route verdicts from Jev answers in code.

Rules are declarative data, shared verbatim with the Rust runtime. Nothing here
evaluates a string as code.
"""

from __future__ import annotations

import operator
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
    verdict, reason, stage, deciding, confidence, _candidate = route_verdict_with_candidate(
        rubric, answers
    )
    return verdict, reason, stage, deciding, confidence


def route_verdict_with_candidate(
    rubric: Rubric, answers: dict[str, dict[str, Any]]
) -> tuple[str, str, str, list[str], float, str | None]:
    """Return routing fields plus any pass/fail candidate downgraded by the floor.

    The first rule whose conditions all hold wins. A rule that cannot be evaluated
    escalates rather than being skipped. The final element is the automatic
    pass/fail candidate when the confidence floor downgraded it to review.
    """
    missing = [answer_id for answer_id in rubric.questions if answer_id not in answers]
    if missing:
        return (
            "escalate",
            "Judgment response is incomplete; missing answers: " + ", ".join(sorted(missing)) + ".",
            _stage_for(rubric, tuple(missing)),
            [],
            0.0,
            None,
        )

    for index, rule in enumerate(rubric.rules):
        if rule.default:
            # The catch-all decided nothing, so there is no confidence to report.
            if rule.verdict in GATED_VERDICTS and 0.0 < rubric.confidence_floor:
                return (
                    "review",
                    (
                        f"{rule.reason} Downgraded from '{rule.verdict}': confidence "
                        f"0.00 is below the {rubric.stakes} floor of "
                        f"{rubric.confidence_floor:.2f}."
                    ),
                    "route",
                    [],
                    0.0,
                    rule.verdict,
                )
            return rule.verdict, rule.reason, "route", [], 0.0, None

        try:
            matched = all(
                _evaluate(condition, answers[condition.answer])
                if condition.answer in answers
                else _raise_missing(index, condition.answer)
                for condition in rule.conditions
            )
        except UnevaluableRule as err:
            answer_id = err.answer_id
            return (
                "escalate",
                f"Could not evaluate '{rule.verdict}' rule: answer '{answer_id}' is missing.",
                _stage_for(rubric, rule.answer_ids),
                [],
                0.0,
                None,
            )

        if not matched:
            continue

        deciding = rule.answer_ids
        confidence = decision_confidence(answers, deciding)
        stage = _stage_for(rubric, deciding)
        floor = rubric.confidence_floor

        if rule.verdict in GATED_VERDICTS and confidence < floor:
            # The rule matched, but not confidently enough to act on automatically.
            return (
                "review",
                (
                    f"{rule.reason} Downgraded from '{rule.verdict}': confidence "
                    f"{confidence:.2f} is below the {rubric.stakes} floor of {floor:.2f}."
                ),
                stage,
                list(deciding),
                confidence,
                rule.verdict,
            )

        return rule.verdict, rule.reason, stage, list(deciding), confidence, None

    # No rule matched and the rubric declared no default.
    return "review", "No routing rule matched.", "route", [], 0.0, None


def _raise_missing(index: int, answer_id: str) -> bool:
    raise UnevaluableRule(index, answer_id)

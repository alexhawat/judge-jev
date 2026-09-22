"""Validated in-memory replay service for CLI, evaluator, and tuning tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from judge_jev.answers import validate_answers
from judge_jev.gates import GateContext, escalate_on_injection, evaluate_replay_gates
from judge_jev.models import GateOutcome, Rubric
from judge_jev.routing import RoutingEvaluation, evaluate_routing


@dataclass(frozen=True)
class RerouteResult:
    verdict: str
    reason: str
    routing: RoutingEvaluation
    gates: tuple[GateOutcome, ...]


def reroute_with_rubric(
    rubric: Rubric,
    answers: Any,
    *,
    historical_gates: list[GateOutcome] | None = None,
    reason_prefix: str = "replay",
) -> RerouteResult:
    """Validate saved answers, route once, and apply the real replay gates.

    This does not perform version/hash drift checks; callers own provenance policy.
    It is the shared mechanics seam for offline explanation, evaluation, and tuning.
    """
    validated = validate_answers(rubric, answers)
    routing = evaluate_routing(rubric, validated)
    gates = evaluate_replay_gates(
        GateContext(
            rubric=rubric,
            filtered_state=None,
            answers=validated,
            verdict=routing.verdict,
            confidence=routing.confidence,
            confidence_floor=rubric.confidence_floor,
            confidence_candidate=routing.confidence_candidate,
            replay=True,
        ),
        historical_gates or [],
    )
    verdict, reason = escalate_on_injection(
        routing.verdict, f"{reason_prefix}: {routing.reason}", gates
    )
    return RerouteResult(verdict, reason, routing, tuple(gates))

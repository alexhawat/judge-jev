"""Deterministic pre- and post-route gates recorded on every JudgmentResult."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from judge_jev.models import GATED_VERDICTS, GateOutcome, Rubric, StateProjection

# Substrings that trigger a deterministic injection warning. Mirrors the mock
# engine's scan so CI and live runs agree on what the gate flags.
_INJECTION_MARKERS = ("ignore all prior", "always return pass")


def state_filter_paths(state_filter: list[Any]) -> list[str]:
    """Normalize rubric state_filter entries to path strings."""
    paths: list[str] = []
    for entry in state_filter:
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict) and "path" in entry:
            paths.append(str(entry["path"]))
        elif hasattr(entry, "path"):
            paths.append(str(entry.path))
    return sorted(paths)


def state_projection_hash(paths: list[str]) -> str:
    """Stable hash of the allowlisted path list."""
    payload = json.dumps(paths, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_state_projection(state_filter: list[Any], filtered_state: dict[str, Any]) -> StateProjection:
    paths = state_filter_paths(state_filter)
    return StateProjection(
        paths=paths,
        hash=state_projection_hash(paths),
        projected_keys=sorted(filtered_state.keys()),
    )


@dataclass
class GateContext:
    """Inputs shared across gate evaluation."""

    rubric: Rubric
    filtered_state: dict[str, Any] | None
    answers: dict[str, dict[str, Any]] | None
    verdict: str | None
    confidence: float | None
    confidence_floor: float
    replay: bool = False
    budget_ok: bool = True
    # Routing reason, so a floor downgrade can be recorded against the matched rule.
    routing_reason: str | None = None


def evaluate_gates(ctx: GateContext) -> list[GateOutcome]:
    """Run deterministic gates and return ordered outcomes."""
    outcomes: list[GateOutcome] = []

    if ctx.replay:
        outcomes.append(GateOutcome("state_projection", "skip", "replay does not re-filter state"))
        outcomes.append(GateOutcome("token_budget", "skip", "replay does not estimate tokens"))
        outcomes.append(GateOutcome("injection_heuristic", "skip", "replay does not scan raw state"))
        outcomes.append(_answer_completeness_gate(ctx.rubric, ctx.answers))
        outcomes.append(
            _confidence_floor_gate(
                ctx.verdict,
                ctx.confidence,
                ctx.confidence_floor,
                ctx.routing_reason,
            )
        )
        return outcomes

    if ctx.filtered_state is not None:
        projection = build_state_projection(ctx.rubric.state_filter, ctx.filtered_state)
        outcomes.append(
            GateOutcome(
                "state_projection",
                "pass",
                f"projected {len(projection.projected_keys)} top-level keys from {len(projection.paths)} paths",
            )
        )
    else:
        outcomes.append(GateOutcome("state_projection", "skip", "no filtered state"))

    if ctx.budget_ok:
        outcomes.append(GateOutcome("token_budget", "pass", "state and questions within budget"))
    else:
        outcomes.append(GateOutcome("token_budget", "fail", "token budget exceeded"))

    outcomes.append(_injection_heuristic_gate(ctx.filtered_state))
    outcomes.append(_answer_completeness_gate(ctx.rubric, ctx.answers))
    outcomes.append(
        _confidence_floor_gate(
            ctx.verdict,
            ctx.confidence,
            ctx.confidence_floor,
            ctx.routing_reason,
        )
    )
    return outcomes


def _injection_heuristic_gate(filtered_state: dict[str, Any] | None) -> GateOutcome:
    if filtered_state is None:
        return GateOutcome("injection_heuristic", "skip", "no state to scan")
    text = json.dumps(filtered_state, ensure_ascii=False).lower()
    hits = [marker for marker in _INJECTION_MARKERS if marker in text]
    if hits:
        return GateOutcome(
            "injection_heuristic",
            "fail",
            f"matched markers: {', '.join(hits)}",
        )
    return GateOutcome("injection_heuristic", "pass", "no known injection markers")


def _answer_completeness_gate(
    rubric: Rubric,
    answers: dict[str, dict[str, Any]] | None,
) -> GateOutcome:
    if answers is None:
        return GateOutcome("answer_completeness", "skip", "no answers yet")
    expected = set(rubric.questions)
    missing = sorted(expected - set(answers))
    if missing:
        return GateOutcome(
            "answer_completeness",
            "fail",
            f"missing answers: {', '.join(missing)}",
        )
    return GateOutcome("answer_completeness", "pass", f"all {len(expected)} questions answered")


def escalate_on_injection(verdict: str, reason: str, gates: list[GateOutcome]) -> tuple[str, str]:
    """Force escalate when the injection heuristic failed and routing did not."""
    failed = any(gate.gate_id == "injection_heuristic" and gate.outcome == "fail" for gate in gates)
    if not failed or verdict == "escalate":
        return verdict, reason
    return "escalate", f"{reason} Injection heuristic failed; verdict escalated."


def _gated_subject(verdict: str, routing_reason: str | None) -> str:
    """The rule verdict the floor applies to, before a downgrade rewrites it to review."""
    if not routing_reason:
        return verdict
    marker = "Downgraded from '"
    index = routing_reason.rfind(marker)
    if index == -1:
        return verdict
    rest = routing_reason[index + len(marker) :]
    end = rest.find("'")
    if end <= 0:
        return verdict
    original = rest[:end]
    if original in GATED_VERDICTS:
        return original
    return verdict


def _confidence_floor_gate(
    verdict: str | None,
    confidence: float | None,
    floor: float,
    routing_reason: str | None = None,
) -> GateOutcome:
    if verdict is None or confidence is None:
        return GateOutcome("confidence_floor", "skip", "routing not complete")
    subject = _gated_subject(verdict, routing_reason)
    if subject not in GATED_VERDICTS:
        return GateOutcome(
            "confidence_floor",
            "skip",
            f"verdict '{verdict}' is not gated by confidence floors",
        )
    if confidence >= floor:
        return GateOutcome(
            "confidence_floor",
            "pass",
            f"confidence {confidence:.2f} meets {floor:.2f} floor",
        )
    if subject != verdict:
        return GateOutcome(
            "confidence_floor",
            "fail",
            f"confidence {confidence:.2f} below {floor:.2f} floor (downgraded {subject} to review)",
        )
    return GateOutcome(
        "confidence_floor",
        "fail",
        f"confidence {confidence:.2f} below {floor:.2f} floor (verdict may downgrade to review)",
    )

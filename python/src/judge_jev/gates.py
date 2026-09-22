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
    confidence_candidate: str | None = None
    replay: bool = False
    budget_ok: bool = True


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
                ctx.confidence_candidate,
                ctx.confidence,
                ctx.confidence_floor,
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
            ctx.confidence_candidate,
            ctx.confidence,
            ctx.confidence_floor,
        )
    )
    return outcomes


def evaluate_replay_gates(
    ctx: GateContext,
    historical_gates: list[GateOutcome],
) -> list[GateOutcome]:
    """Combine original input-only evidence with current answer-derived gates."""
    current = evaluate_gates(ctx)
    historical_by_id = {
        gate.gate_id: gate
        for gate in historical_gates
        if gate.gate_id in {"state_projection", "token_budget", "injection_heuristic"}
    }
    for index, gate_id in enumerate(
        ("state_projection", "token_budget", "injection_heuristic")
    ):
        historical = historical_by_id.get(gate_id)
        unavailable_reason = f"replay lacks raw state and historical {gate_id} evidence"
        if (
            historical is None
            or historical.reason == unavailable_reason
            or historical.reason.startswith("replay does not ")
        ):
            current[index] = GateOutcome(gate_id, "skip", unavailable_reason)
            continue

        reason = historical.reason
        prefix = "historical evidence from original run: "
        while reason.startswith(prefix):
            reason = reason[len(prefix) :]
        current[index] = GateOutcome(
            historical.gate_id,
            historical.outcome,
            f"{prefix}{reason}",
        )
    return current


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


def _confidence_floor_gate(
    verdict: str | None,
    confidence_candidate: str | None,
    confidence: float | None,
    floor: float,
) -> GateOutcome:
    if verdict is None or confidence is None:
        return GateOutcome("confidence_floor", "skip", "routing not complete")
    subject = confidence_candidate or verdict
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
    if confidence_candidate is not None and verdict == "review":
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

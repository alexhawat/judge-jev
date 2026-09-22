"""Human-readable output and offline explanation built from routing trace data."""

from __future__ import annotations

from typing import Any

from judge_jev.models import GateOutcome
from judge_jev.reroute import reroute_with_rubric
from judge_jev.rubric import Rubric, rubric_content_hash


def next_step(verdict: str) -> str:
    return {
        "pass": "Proceed, while keeping normal review appropriate to the task.",
        "fail": "Revise the artifact before using or publishing it.",
        "review": "Have a person review the uncertain or borderline checks.",
        "escalate": "Stop and ask an appropriate reviewer to inspect the flagged risk.",
        "skip": "Provide a usable artifact, then judge again.",
    }.get(verdict, "Inspect the result before continuing.")


def format_result(
    result: dict[str, Any], *, history_id: str | None = None, action: str = "judgment"
) -> str:
    verdict = str(result.get("verdict", "unknown"))
    if result.get("mock") and action == "judgment":
        heading = f"DEMO — CANNED ANSWERS — {verdict.upper()}"
    else:
        heading = verdict.upper()
    lines = [f"{heading} — {result.get('routing_reason', 'No routing reason reported.')}", ""]
    answers = result.get("answers") or {}
    deciding = result.get("deciding_answers") or []
    for answer_id in deciding:
        answer = answers.get(answer_id) or {}
        label = answer_id.rsplit(".", 1)[-1].replace("_", " ").capitalize()
        if answer.get("type") == "score":
            legend = answer.get("legend") or {}
            maximum = max((int(key) for key in legend if str(key).isdigit()), default="?")
            lines.append(
                f"{label:<28} {float(answer.get('score', 0)):.2f} / {maximum}  "
                f"confidence {float(answer.get('confidence', 0)):.2f}"
            )
        elif answer.get("type") == "choice":
            lines.append(
                f"{label:<28} {answer.get('choice')}  "
                f"confidence {float(answer.get('confidence', 0)):.2f}"
            )
        elif answer.get("type") == "noul":
            lines.append(f"{label:<28} yes-probability {float(answer.get('noul', 0)):.2f}")
    if action in {"replay", "history"}:
        mode = "offline replay/view; no API call was made"
        evidence = "canned mock answers" if result.get("mock") else "recorded API answers"
    else:
        mode = "offline canned demo" if result.get("mock") else "live API judgment"
        evidence = None
    lines.extend(
        [
            f"Decision confidence           {float(result.get('confidence', 0.0)):.2f} (required {float(result.get('confidence_floor', 0.0)):.2f})",
            f"Mode                          {mode}",
        ]
    )
    if evidence:
        lines.append(f"Original evidence             {evidence}")
    gates = result.get("deterministic_gates") or []
    injection = next((gate for gate in gates if gate.get("gate_id") == "injection_heuristic"), None)
    if injection and injection.get("outcome") == "fail":
        lines.append(f"Deterministic override        injection heuristic: {injection.get('reason')}")
        lines.append("Override confidence           not a calibrated model probability")
    if result.get("mock"):
        lines.append("Limitation                    canned answers prove mechanics, not judgment quality")
    if result.get("rubric_hash") is None:
        lines.append("Limitation                    legacy evidence has no rubric content hash")
    if history_id:
        lines.append(f"Saved result                  {history_id} (result only; no raw input)")
    if result.get("mock") and action == "judgment":
        lines.extend(
            ["", "Next: Try a real judgment: set TYPESAFE_API_KEY and run without --mock."]
        )
    else:
        lines.extend(["", f"Next: {next_step(verdict)}"])
    return "\n".join(lines)


def explain_result(saved: dict[str, Any], rubric: Rubric) -> dict[str, Any]:
    historical = [
        GateOutcome(str(g["gate_id"]), str(g["outcome"]), str(g["reason"]))
        for g in saved.get("deterministic_gates") or []
    ]
    rerouted = reroute_with_rubric(rubric, saved.get("answers"), historical_gates=historical)
    current_hash = rubric_content_hash(rubric)
    saved_hash = saved.get("rubric_hash")
    limitations = []
    if saved_hash is None:
        limitations.append("Legacy result has no rubric_hash; same-version content drift is unknown.")
    elif saved_hash != current_hash:
        limitations.append("Rubric content differs; this explanation shows current routing, not the original rules.")
    injection = next((g for g in rerouted.gates if g.gate_id == "injection_heuristic"), None)
    override = bool(injection and injection.outcome == "fail" and rerouted.routing.verdict != rerouted.verdict)
    return {
        "saved_verdict": saved.get("verdict"),
        "current_verdict": rerouted.verdict,
        "routing_verdict_before_gates": rerouted.routing.verdict,
        "routing_reason": rerouted.reason,
        "matched_rule_id": rerouted.routing.matched_rule_id,
        "rules": [rule.to_dict() for rule in rerouted.routing.rules],
        "deciding_answers": list(rerouted.routing.deciding_answers),
        "confidence": rerouted.routing.confidence,
        "confidence_floor": rubric.confidence_floor,
        "deterministic_override": "injection_heuristic" if override else None,
        "gates": [gate.to_dict() for gate in rerouted.gates],
        "rubric_hash": current_hash,
        "saved_rubric_hash": saved_hash,
        "limitations": limitations,
    }


def format_explanation(explanation: dict[str, Any]) -> str:
    lines = [
        f"Decision: {str(explanation['current_verdict']).upper()}",
        f"Matched rule: {explanation.get('matched_rule_id') or '-'}",
        f"Confidence: {explanation['confidence']:.2f} (floor {explanation['confidence_floor']:.2f})",
    ]
    if explanation.get("deterministic_override"):
        lines.append(
            "Deterministic override: injection_heuristic changed the routed verdict; its result is not a calibrated confidence."
        )
    lines.append("")
    for rule in explanation["rules"]:
        lines.append(f"{rule['rule_id']} {rule['verdict']}: {rule['outcome']}")
        for comparison in rule["comparisons"]:
            status = "unevaluated" if not comparison["evaluated"] else str(comparison["matched"]).lower()
            lines.append(
                f"  {comparison['answer']}.{comparison['field']} {comparison['op']} "
                f"{comparison['expected']!r}: {status}"
            )
    for limitation in explanation["limitations"]:
        lines.append(f"Limitation: {limitation}")
    return "\n".join(lines)

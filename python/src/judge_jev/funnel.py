"""Judgment funnel: screen -> profile -> locate -> score -> route."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from judge_jev.answers import validate_answers
from judge_jev.budget import check_budget
from judge_jev.canonical import CanonicalState
from judge_jev.gates import (
    GateContext,
    build_state_projection,
    escalate_on_injection,
    evaluate_gates,
    evaluate_replay_gates,
    state_projection_hash,
)
from judge_jev.logfire_tracing import span_gates, span_judge_run, span_route, span_system_one
from judge_jev.models import (
    RUNTIME_NAME,
    RUNTIME_VERSION,
    GateOutcome,
    JudgmentResult,
    Runtime,
    StateProjection,
    Usage,
)
from judge_jev.paths import repo_root
from judge_jev.routing import route_verdict_with_candidate
from judge_jev.rubric import load_rubric
from judge_jev.state_filter import filter_state
from judge_jev.typesafe_client import (
    MOCK_ANSWERS_KEY,
    JudgeJevError,
    build_questions,
    get_engine,
)


# `--input -` reads stdin, so `judge-jev run ... | judge-jev replay --input -`
# composes. It is also the only way to judge something that was never a file.
STDIN_PATH = "-"


def resolve_input_path(path: Path) -> Path:
    """Find the file a caller named, relative to the cwd or to the repo root.

    `-` is stdin, not a path: without this guard the repo-relative fallback goes
    looking for a file literally named `-` under the repo root.
    """
    if str(path) == STDIN_PATH:
        return path
    if path.is_file():
        return path
    candidate = repo_root() / path
    if candidate.is_file():
        return candidate
    return path


def read_input_text(path: Path) -> str:
    if str(path) == STDIN_PATH:
        return sys.stdin.read()
    try:
        return resolve_input_path(path).read_text(encoding="utf-8")
    except OSError as err:
        # Worded, down to the "(os error N)" tail, exactly as the Rust runtime
        # words it: the message a caller greps for must not depend on which
        # runtime .judge-jev/runtime happens to name.
        detail = f"{err.strerror} (os error {err.errno})" if err.errno else str(err)
        raise JudgeJevError(f"cannot read input {path}: {detail}") from err


def load_input(path: Path) -> dict[str, Any]:
    text = read_input_text(path)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise JudgeJevError(f"input {path} is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise JudgeJevError(f"input {path} must be a JSON object, got {type(data).__name__}")
    return data


def run_judgment(
    rubric_id: str,
    input_path: Path,
    *,
    mock: bool = False,
    tracing_active: bool = False,
) -> JudgmentResult:
    rubric = load_rubric(rubric_id)
    raw = load_input(input_path)
    # Canonical from here on: every request is built from these exact bytes, and
    # both runtimes build the same ones.
    try:
        filtered = filter_state(raw, rubric.state_filter)
        state = CanonicalState.of(filtered)
    except ValueError as err:
        # NaN and Infinity reach here from json.loads, which accepts them; the Rust
        # runtime's parser rejects them outright. Either way, no judgment happens.
        raise JudgeJevError(f"state cannot be serialized for the request: {err}") from err

    projection = build_state_projection(rubric.state_filter, filtered)

    pinned = raw.get(MOCK_ANSWERS_KEY) if mock else None
    if pinned is not None and not isinstance(pinned, dict):
        raise JudgeJevError(f"{MOCK_ANSWERS_KEY} must be a JSON object of answer overrides")

    engine = get_engine(mock, pinned)
    model = rubric.model
    questions = build_questions(rubric)

    # Before the request is built: an oversized state is a local failure, not a
    # round trip that comes back as an opaque API error.
    check_budget(state, rubric)

    with span_judge_run(
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        model=model,
        mock=mock,
        active=tracing_active,
    ):
        logger.info(
            "funnel start rubric={} model={} mock={} questions={}",
            rubric.id,
            model,
            mock,
            len(questions),
        )

        with span_system_one(
            question_count=len(questions),
            model=model,
            mock=mock,
            state_bytes=len(state.text.encode("utf-8")),
            active=tracing_active,
        ):
            try:
                answers, usage, request_id, answered_by = engine.system_one(state, questions, model)
            except JudgeJevError:
                raise
            except Exception as err:  # noqa: BLE001 - API/engine failures must not become verdicts.
                raise JudgeJevError(f"system_one failed: {err}") from err
            validate_answers(rubric, answers)

        verdict, reason, stage, deciding, confidence, confidence_candidate = (
            route_verdict_with_candidate(rubric, answers)
        )

        gate_ctx = GateContext(
            rubric=rubric,
            filtered_state=filtered,
            answers=answers,
            verdict=verdict,
            confidence=confidence,
            confidence_floor=rubric.confidence_floor,
            confidence_candidate=confidence_candidate,
            replay=False,
            budget_ok=True,
        )
        gate_outcomes = evaluate_gates(gate_ctx)
        verdict, reason = escalate_on_injection(verdict, reason, gate_outcomes)

        with span_gates(gate_outcomes, active=tracing_active):
            pass

        with span_route(
            verdict=verdict,
            confidence=confidence,
            deciding_answers=deciding,
            routing_reason=reason,
            active=tracing_active,
        ):
            result = JudgmentResult(
                rubric_id=rubric.id,
                rubric_version=rubric.version,
                verdict=verdict,
                confidence=confidence,
                stage=stage,
                model=answered_by,
                usage=usage,
                answers=answers,
                routing_reason=reason,
                mock=mock,
                deciding_answers=deciding,
                confidence_floor=rubric.confidence_floor,
                request_id=request_id,
                state_projection=projection,
                deterministic_gates=gate_outcomes,
            )

    logger.info(
        "funnel complete verdict={} stage={} confidence={:.2f} deciding={}",
        verdict,
        stage,
        confidence,
        ",".join(deciding) or "-",
    )
    return result


def version_drift(saved: dict[str, Any], rubric_version: str) -> str | None:
    """The message explaining why replaying *saved* would not re-derive its verdict.

    `replay` exists to re-derive a verdict without paying for another call: for an
    audit, for testing a threshold change, for explaining a past decision. All three
    break silently if the rules moved underneath the answers, so a mismatch is
    reported rather than routed. A result carrying no version at all cannot be
    checked, which is the same failure wearing a different hat.
    """
    saved_version = saved.get("rubric_version")
    if saved_version is None:
        return (
            f"saved result carries no rubric_version, so it cannot be shown to have "
            f"been judged under {saved['rubric_id']} {rubric_version}"
        )
    if str(saved_version) != rubric_version:
        return (
            f"saved result was judged under {saved['rubric_id']} {saved_version}, "
            f"but {rubric_version} is on disk"
        )
    return None


def replay_judgment(saved: dict[str, Any], *, allow_version_drift: bool = False) -> JudgmentResult:
    """Re-route from saved answers without calling TypeSafe."""
    if not isinstance(saved, dict) or "rubric_id" not in saved or "answers" not in saved:
        raise JudgeJevError("Replay input must be a JudgmentResult object with rubric_id and answers")

    rubric = load_rubric(saved["rubric_id"])
    drift = version_drift(saved, rubric.version)
    if drift is not None and not allow_version_drift:
        raise JudgeJevError(f"{drift}; re-run with --allow-version-drift to route it anyway")

    answers = validate_answers(rubric, saved["answers"])
    verdict, reason, stage, deciding, confidence, confidence_candidate = (
        route_verdict_with_candidate(rubric, answers)
    )
    # Under drift the result records the version it was ROUTED under, so the reason
    # is the only place the original version survives. Say it there.
    prefix = f"replay ({drift})" if drift is not None else "replay"
    usage_data = saved.get("usage", {}) or {}
    usage = Usage(
        input_tokens=usage_data.get("input_tokens"),
        output_tokens=usage_data.get("output_tokens"),
    )

    projection_data = saved.get("state_projection") or {}
    paths = [str(path) for path in (projection_data.get("paths") or [])]
    raw_hash = projection_data.get("hash")
    projection = StateProjection(
        paths=paths,
        hash=str(raw_hash) if raw_hash else state_projection_hash(paths),
        projected_keys=[str(key) for key in (projection_data.get("projected_keys") or [])],
    )
    historical_gates = [
        GateOutcome(g["gate_id"], g["outcome"], g["reason"])
        for g in saved.get("deterministic_gates") or []
    ]
    gate_outcomes = evaluate_replay_gates(
        GateContext(
            rubric=rubric,
            filtered_state=None,
            answers=answers,
            verdict=verdict,
            confidence=confidence,
            confidence_floor=rubric.confidence_floor,
            confidence_candidate=confidence_candidate,
            replay=True,
        ),
        historical_gates,
    )

    verdict, published_reason = escalate_on_injection(verdict, f"{prefix}: {reason}", gate_outcomes)
    return JudgmentResult(
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        verdict=verdict,
        confidence=confidence,
        stage=stage,
        model=saved.get("model", rubric.model),
        usage=usage,
        answers=answers,
        routing_reason=published_reason,
        mock=saved.get("mock", False),
        deciding_answers=deciding,
        confidence_floor=rubric.confidence_floor,
        request_id=saved.get("request_id"),
        runtime=Runtime(RUNTIME_NAME, RUNTIME_VERSION),
        state_projection=projection,
        deterministic_gates=gate_outcomes,
    )

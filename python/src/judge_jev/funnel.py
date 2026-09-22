"""Judgment funnel: screen -> profile -> locate -> score -> route."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from judge_jev.answers import validate_answers
from judge_jev.backend import BackendResponse, make_backend, validate_capabilities
from judge_jev.budget import check_budget
from judge_jev.canonical import CanonicalState, strict_json_loads
from judge_jev.gates import (
    GateContext,
    build_state_projection,
    escalate_on_injection,
    evaluate_gates,
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
from judge_jev.reroute import reroute_with_rubric
from judge_jev.rubric import load_rubric, rubric_content_hash
from judge_jev.state_filter import filter_state
from judge_jev.typesafe_client import MOCK_ANSWERS_KEY, JudgeJevError, build_questions, get_engine


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
        data = strict_json_loads(text)
    except (json.JSONDecodeError, ValueError) as err:
        raise JudgeJevError(f"input {path} is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise JudgeJevError(f"input {path} must be a JSON object, got {type(data).__name__}")
    return data


def validate_shipped_input_shape(rubric_id: str, raw: dict[str, Any]) -> None:
    """Reject present values the shipped rubric cannot interpret consistently."""
    if rubric_id == "assistant-reply":
        expected: dict[str, type] = {"prompt": str, "reply": str}
    elif rubric_id == "agent-trajectory":
        expected = {"goal": str, "steps": list, "final_output": str}
    else:
        return
    for name, kind in expected.items():
        value = raw.get(name)
        if value is not None and not isinstance(value, kind):
            if isinstance(value, bool):
                actual = "boolean"
            elif isinstance(value, dict):
                actual = "object"
            elif isinstance(value, list):
                actual = "array"
            elif isinstance(value, str):
                actual = "string"
            elif isinstance(value, (int, float)):
                actual = "number"
            else:
                actual = type(value).__name__
            wanted = "array" if kind is list else "string"
            raise JudgeJevError(
                f"input field '{name}' must be a {wanted} or null, got {actual}"
            )


def run_judgment(
    rubric_id: str,
    input_path: Path,
    *,
    mock: bool = False,
    backend_id: str | None = None,
    replay_input: Path | None = None,
    capture_dir: Path | None = None,
    capture_redact: list[str] | None = None,
    tracing_active: bool = False,
) -> JudgmentResult:
    rubric = load_rubric(rubric_id)
    raw = load_input(input_path)
    validate_shipped_input_shape(rubric.id, raw)
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

    selected_backend = backend_id or ("replay" if mock else "typesafe")
    # Keep the old injectable engine seam for library callers and existing tests;
    # the public CLI always resolves and passes an explicit backend id.
    engine = (
        get_engine(mock, pinned)
        if backend_id is None and replay_input is None
        else make_backend(selected_backend, pinned=pinned, replay_input=replay_input)
    )
    model = rubric.model
    questions = build_questions(rubric)
    if hasattr(engine, "capabilities"):
        validate_capabilities(engine.capabilities, questions)
    recorded = getattr(engine, "recorded", None)
    if recorded is not None:
        if recorded.get("rubric_id") != rubric.id or str(recorded.get("rubric_version")) != rubric.version:
            raise JudgeJevError("recorded replay rubric id/version does not match the requested rubric")
        recorded_state = recorded.get("state")
        if recorded_state is None:
            raise JudgeJevError(
                "recorded replay needs captured filtered state to prove it belongs to this input"
            )
        if CanonicalState.of(recorded_state).text != state.text:
            raise JudgeJevError("recorded replay state does not match the newly filtered input")
        if recorded.get("rubric_hash") != rubric_content_hash(rubric):
            raise JudgeJevError("recorded replay rubric hash does not match the loaded rubric")

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
                raw_response = engine.system_one(state, questions, model)
                if isinstance(raw_response, BackendResponse):
                    backend_response = raw_response
                else:
                    legacy_answers, legacy_usage, legacy_request, legacy_model = raw_response
                    backend_response = BackendResponse(
                        legacy_answers,
                        legacy_usage,
                        legacy_request,
                        model,
                        legacy_model,
                        "canned_demo" if mock else "live_model",
                    )
            except JudgeJevError:
                raise
            except Exception as err:  # noqa: BLE001 - API/engine failures must not become verdicts.
                raise JudgeJevError(f"system_one failed: {err}") from err
            answers = backend_response.answers
            usage = backend_response.usage
            request_id = backend_response.request_id
            answered_by = backend_response.resolved_model
            if not answers:
                raise JudgeJevError("system_one returned no answers")
            answers = validate_answers(rubric, answers)

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
                rubric_hash=rubric_content_hash(rubric),
                verdict=verdict,
                confidence=confidence,
                stage=stage,
                model=answered_by,
                usage=usage,
                answers=answers,
                routing_reason=reason,
                mock=mock,
                backend=selected_backend,
                requested_model=backend_response.requested_model,
                backend_provenance=backend_response.provenance,
                deciding_answers=deciding,
                confidence_floor=rubric.confidence_floor,
                request_id=request_id,
                source_rubric_version=rubric.version,
                source_rubric_hash=rubric_content_hash(rubric),
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
    if capture_dir is not None:
        # Capture is diagnostic evidence, never part of the judgment transaction.
        # Every setup/serialization/storage failure is counted or logged and cannot
        # change the already-computed result or its exit code.
        try:
            from judge_jev.capture import CaptureWriter, build_record

            writer = CaptureWriter(capture_dir)
            record = build_record(
                result,
                rubric,
                filtered,
                redact_paths=capture_redact,
                rule_counts=writer.rule_counts,
            )
            writer.enqueue(record)
            stats = writer.close()
            logger.info(
                "capture stats written={} dropped={} errors={}",
                stats.written,
                stats.dropped,
                stats.errors,
            )
        except Exception as err:  # noqa: BLE001 - capture never changes a verdict.
            logger.error("capture failed safely: {}", err)
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
    current_hash = rubric_content_hash(rubric)
    saved_hash = saved.get("rubric_hash")
    hash_drift = None
    if saved_hash is not None and str(saved_hash) != current_hash:
        hash_drift = (
            f"saved result carries rubric_hash {saved_hash}, but {current_hash} is on disk"
        )
    blocking_drift = drift or hash_drift
    if blocking_drift is not None and not allow_version_drift:
        raise JudgeJevError(
            f"{blocking_drift}; re-run with --allow-version-drift to route it anyway"
        )

    answers = validate_answers(rubric, saved["answers"])
    # Under drift the result records the version it was ROUTED under, so the reason
    # is the only place the original version survives. Say it there.
    provenance_note = blocking_drift
    if saved_hash is None:
        legacy = "saved result carries no rubric_hash, so content drift cannot be checked"
        provenance_note = f"{provenance_note}; {legacy}" if provenance_note else legacy
    prefix = f"replay ({provenance_note})" if provenance_note is not None else "replay"
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
    rerouted = reroute_with_rubric(
        rubric, answers, historical_gates=historical_gates, reason_prefix=prefix
    )
    verdict = rerouted.verdict
    published_reason = rerouted.reason
    stage = rerouted.routing.stage
    deciding = list(rerouted.routing.deciding_answers)
    confidence = rerouted.routing.confidence
    gate_outcomes = list(rerouted.gates)
    return JudgmentResult(
        rubric_id=rubric.id,
        rubric_version=rubric.version,
        rubric_hash=current_hash,
        verdict=verdict,
        confidence=confidence,
        stage=stage,
        model=saved.get("model", rubric.model),
        usage=usage,
        answers=answers,
        routing_reason=published_reason,
        mock=saved.get("mock", False),
        backend=str(saved.get("backend") or ("replay" if saved.get("mock") else "typesafe")),
        requested_model=saved.get("requested_model") or saved.get("model"),
        backend_provenance="recorded_model",
        deciding_answers=deciding,
        confidence_floor=rubric.confidence_floor,
        request_id=saved.get("request_id"),
        source_rubric_version=saved.get("source_rubric_version", saved.get("rubric_version")),
        source_rubric_hash=saved.get("source_rubric_hash", saved.get("rubric_hash")),
        runtime=Runtime(RUNTIME_NAME, RUNTIME_VERSION),
        state_projection=projection,
        deterministic_gates=gate_outcomes,
    )

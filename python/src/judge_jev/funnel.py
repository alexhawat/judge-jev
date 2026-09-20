"""Judgment funnel: screen -> profile -> locate -> score -> route."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from judge_jev.models import JudgmentResult, Usage
from judge_jev.paths import repo_root
from judge_jev.routing import route_verdict
from judge_jev.rubric import load_rubric
from judge_jev.typesafe_client import (
    MOCK_ANSWERS_KEY,
    JudgeJevError,
    build_questions,
    filter_state,
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
) -> JudgmentResult:
    rubric = load_rubric(rubric_id)
    raw = load_input(input_path)
    state = filter_state(raw, rubric.state_filter)

    pinned = raw.get(MOCK_ANSWERS_KEY) if mock else None
    if pinned is not None and not isinstance(pinned, dict):
        raise JudgeJevError(f"{MOCK_ANSWERS_KEY} must be a JSON object of answer overrides")

    engine = get_engine(mock, pinned)
    model = rubric.model
    questions = build_questions(rubric)

    logger.info(
        "funnel start rubric={} model={} mock={} questions={}",
        rubric.id,
        model,
        mock,
        len(questions),
    )

    answers, usage, request_id = engine.system_one(state, questions, model)
    verdict, reason, stage, deciding, confidence = route_verdict(rubric, answers)

    result = JudgmentResult(
        rubric_id=rubric.id,
        verdict=verdict,
        confidence=confidence,
        stage=stage,
        model=model,
        usage=usage,
        answers=answers,
        routing_reason=reason,
        mock=mock,
        deciding_answers=deciding,
        confidence_floor=rubric.confidence_floor,
        request_id=request_id,
    )
    logger.info(
        "funnel complete verdict={} stage={} confidence={:.2f} deciding={}",
        verdict,
        stage,
        confidence,
        ",".join(deciding) or "-",
    )
    return result


def replay_judgment(saved: dict[str, Any]) -> JudgmentResult:
    """Re-route from saved answers without calling TypeSafe."""
    if not isinstance(saved, dict) or "rubric_id" not in saved or "answers" not in saved:
        raise JudgeJevError("Replay input must be a JudgmentResult object with rubric_id and answers")

    rubric = load_rubric(saved["rubric_id"])
    answers = saved["answers"]
    verdict, reason, stage, deciding, confidence = route_verdict(rubric, answers)
    usage_data = saved.get("usage", {}) or {}
    usage = Usage(
        input_tokens=usage_data.get("input_tokens"),
        output_tokens=usage_data.get("output_tokens"),
    )
    return JudgmentResult(
        rubric_id=rubric.id,
        verdict=verdict,
        confidence=confidence,
        stage=stage,
        model=saved.get("model", rubric.model),
        usage=usage,
        answers=answers,
        routing_reason=f"replay: {reason}",
        mock=saved.get("mock", False),
        deciding_answers=deciding,
        confidence_floor=rubric.confidence_floor,
        request_id=saved.get("request_id"),
    )

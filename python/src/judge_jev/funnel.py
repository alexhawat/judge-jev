"""Judgment funnel: screen → profile → locate → score → route."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from judge_jev.models import JudgmentResult, Rubric, Usage
from judge_jev.paths import repo_root
from judge_jev.routing import aggregate_confidence, route_verdict
from judge_jev.rubric import load_rubric
from judge_jev.typesafe_client import build_questions, filter_state, get_engine


def resolve_input_path(path: Path) -> Path:
    if path.is_file():
        return path
    candidate = repo_root() / path
    if candidate.is_file():
        return candidate
    return path


def load_input(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Input JSON must be an object")
    return data


def run_judgment(
    rubric_id: str,
    input_path: Path,
    *,
    mock: bool = False,
) -> JudgmentResult:
    rubric = load_rubric(rubric_id)
    raw = load_input(resolve_input_path(input_path))
    state = filter_state(raw, rubric.state_filter) if rubric.state_filter else raw

    engine = get_engine(mock)
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
    verdict, reason, stage = route_verdict(rubric, answers)
    confidence = aggregate_confidence(answers)

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
        request_id=request_id,
    )
    logger.info("funnel complete verdict={} stage={} confidence={:.2f}", verdict, stage, confidence)
    return result


def replay_judgment(saved: dict[str, Any]) -> JudgmentResult:
    """Re-route from saved answers without calling TypeSafe."""
    rubric = load_rubric(saved["rubric_id"])
    answers = saved["answers"]
    verdict, reason, stage = route_verdict(rubric, answers)
    usage_data = saved.get("usage", {})
    usage = Usage(
        input_tokens=usage_data.get("input_tokens"),
        output_tokens=usage_data.get("output_tokens"),
    )
    return JudgmentResult(
        rubric_id=rubric.id,
        verdict=verdict,
        confidence=aggregate_confidence(answers),
        stage=stage,
        model=saved.get("model", rubric.model),
        usage=usage,
        answers=answers,
        routing_reason=f"replay: {reason}",
        mock=saved.get("mock", False),
        request_id=saved.get("request_id"),
    )

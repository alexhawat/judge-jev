"""Validation at the answer boundary shared by live, mock, and replay paths."""

from __future__ import annotations

import math
from typing import Any

from judge_jev.models import JudgeJevError, Rubric


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JudgeJevError(f"{where} must be a number, got {type(value).__name__}")
    result = float(value)
    if not math.isfinite(result):
        raise JudgeJevError(f"{where} must be finite")
    return result


def _unit(value: Any, where: str) -> float:
    result = _number(value, where)
    if not 0.0 <= result <= 1.0:
        raise JudgeJevError(f"{where} must be between 0 and 1")
    return result


def _probabilities(value: Any, where: str, allowed: set[str]) -> None:
    if not isinstance(value, dict):
        raise JudgeJevError(f"{where} must be an object")
    for key, probability in value.items():
        if not isinstance(key, str) or key not in allowed:
            raise JudgeJevError(
                f"{where} has invalid key {key!r}; expected one of {sorted(allowed)}"
            )
        _unit(probability, f"{where}.{key}")


def validate_answers(
    rubric: Rubric, answers: Any, *, require_any: bool = True
) -> dict[str, dict[str, Any]]:
    """Validate every present expected answer without inventing missing answers.

    Missing expected answers remain a routing concern and always escalate. A wholly
    empty response is an operational failure because no judgment was returned.
    """
    if not isinstance(answers, dict):
        raise JudgeJevError(
            f"system_one answers must be a JSON object, got {type(answers).__name__}"
        )
    if require_any and not answers:
        raise JudgeJevError("system_one returned no answers")

    unknown = sorted(set(answers) - set(rubric.questions))
    if unknown:
        raise JudgeJevError(f"answers contain unknown question ids: {', '.join(unknown)}")

    for answer_id, answer in answers.items():
        where = f"answers.{answer_id}"
        if not isinstance(answer, dict):
            raise JudgeJevError(f"{where} must be an object")
        question = rubric.questions[answer_id]
        expected = question["type"]
        actual = answer.get("type")
        if actual != expected:
            raise JudgeJevError(f"{where}.type must be {expected!r}, got {actual!r}")

        if expected == "noul":
            if "noul" not in answer:
                raise JudgeJevError(f"{where} is missing required field 'noul'")
            _unit(answer["noul"], f"{where}.noul")
            continue

        for field in ("confidence", "probabilities"):
            if field not in answer:
                raise JudgeJevError(f"{where} is missing required field {field!r}")
        _unit(answer["confidence"], f"{where}.confidence")

        criteria = question["criteria"]
        if expected == "choice":
            if "choice" not in answer:
                raise JudgeJevError(f"{where} is missing required field 'choice'")
            choice = answer["choice"]
            allowed = {str(label) for label in criteria}
            if not isinstance(choice, str) or choice not in allowed:
                raise JudgeJevError(
                    f"{where}.choice must be one of {sorted(allowed)}, got {choice!r}"
                )
            _probabilities(answer["probabilities"], f"{where}.probabilities", allowed)
        else:
            if "score" not in answer:
                raise JudgeJevError(f"{where} is missing required field 'score'")
            score = _number(answer["score"], f"{where}.score")
            highest = len(criteria) - 1
            if not 0.0 <= score <= highest:
                raise JudgeJevError(f"{where}.score must be between 0 and {highest}")
            allowed = {str(index) for index in range(len(criteria))}
            _probabilities(answer["probabilities"], f"{where}.probabilities", allowed)
            legend = answer.get("legend")
            if legend is not None:
                if not isinstance(legend, dict) or any(str(key) not in allowed for key in legend):
                    raise JudgeJevError(f"{where}.legend keys must be valid score levels")
    return answers

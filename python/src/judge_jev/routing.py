"""Route verdicts from Jev answers in code."""

from __future__ import annotations

from typing import Any

from judge_jev.models import Rubric


class AnswerView:
    """Attribute-style access to normalized answers for routing expressions."""

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self._answers = answers

    def __getitem__(self, key: str) -> "_AnswerProxy":
        return _AnswerProxy(self._answers.get(key, {}))


class _AnswerProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)


def _confidence_for_rule(answers: dict[str, dict[str, Any]], verdict: str) -> float:
    confidences: list[float] = []
    for answer in answers.values():
        if answer.get("type") == "choice" and "confidence" in answer:
            confidences.append(float(answer["confidence"]))
        elif answer.get("type") == "score" and "confidence" in answer:
            confidences.append(float(answer["confidence"]))
        elif answer.get("type") == "noul" and "noul" in answer:
            noul = float(answer["noul"])
            confidences.append(abs(noul - 0.5) * 2)
    if not confidences:
        return 0.5
    return max(confidences)


def route_verdict(rubric: Rubric, answers: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
    """Return (verdict, reason, stage)."""
    view = {"answers": AnswerView(answers)}
    rules = rubric.routing.get("rules", [])
    for rule in rules:
        when = rule["when"]
        if when == "default":
            return rule["verdict"], rule.get("reason", ""), _stage_for_verdict(rule["verdict"])
        try:
            if eval(when, {"__builtins__": {}}, view):  # noqa: S307
                return rule["verdict"], rule.get("reason", ""), _stage_for_verdict(rule["verdict"])
        except Exception:
            continue
    return "review", "No routing rule matched.", "route"


def _stage_for_verdict(verdict: str) -> str:
    if verdict == "skip":
        return "screen"
    return "route"


def aggregate_confidence(answers: dict[str, dict[str, Any]]) -> float:
    return _confidence_for_rule(answers, "pass")

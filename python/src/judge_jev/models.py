"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Funnel stages, earliest first. A rule's stage is the earliest stage among the
# questions it reads.
STAGE_ORDER = ("screen", "profile", "locate", "score", "route")

VERDICTS = ("pass", "fail", "review", "escalate", "skip")

# Verdicts that assert an automatic conclusion and are therefore gated by the
# rubric's confidence floor. review/escalate/skip already defer to a human.
GATED_VERDICTS = ("pass", "fail")


class RubricError(ValueError):
    """A rubric is malformed. Raised at load time, never at judgment time."""


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Condition:
    """One comparison against a single answer field."""

    answer: str
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class RoutingRule:
    verdict: str
    reason: str
    conditions: tuple[Condition, ...] = ()
    default: bool = False

    @property
    def answer_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for condition in self.conditions:
            if condition.answer not in seen:
                seen.append(condition.answer)
        return tuple(seen)


@dataclass
class JudgmentResult:
    rubric_id: str
    verdict: str
    confidence: float
    stage: str
    model: str
    usage: Usage
    answers: dict[str, dict[str, Any]]
    routing_reason: str
    mock: bool
    # Answer IDs the matched rule read. These, and only these, determine `confidence`.
    deciding_answers: list[str] = field(default_factory=list)
    # The floor `confidence` was checked against, from confidence_floors[stakes].
    confidence_floor: float = 0.0
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["usage"] = self.usage.to_dict()
        return data


@dataclass
class Rubric:
    id: str
    version: str
    description: str
    model: str
    stakes: str
    confidence_floors: dict[str, float]
    state_filter: list[str]
    questions: dict[str, dict[str, Any]]
    rules: tuple[RoutingRule, ...] = ()

    @property
    def confidence_floor(self) -> float:
        """The floor automatic verdicts must clear, from this rubric's stakes."""
        return float(self.confidence_floors.get(self.stakes, 0.0))

    def stage_of(self, answer_id: str) -> str | None:
        question = self.questions.get(answer_id)
        return question.get("stage") if question else None

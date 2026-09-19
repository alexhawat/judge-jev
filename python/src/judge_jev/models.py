"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    routing: dict[str, Any] = field(default_factory=dict)

"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Funnel stages, earliest first. A rule's stage is the earliest stage among the
# questions it reads.
STAGE_ORDER = ("screen", "profile", "locate", "score", "route")

VERDICTS = ("pass", "fail", "review", "escalate", "skip")

RUNTIME_NAME = "python"


def _runtime_version() -> str:
    """This build's version, from installed metadata with the pyproject value as a
    fallback so a source checkout still reports something truthful."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("judge-jev")
    except Exception:  # noqa: BLE001 - provenance must never break a judgment.
        return "0.0.0+unknown"


RUNTIME_VERSION = _runtime_version()

# Verdicts that assert an automatic conclusion and are therefore gated by the
# rubric's confidence floor. review/escalate/skip already defer to a human.
GATED_VERDICTS = ("pass", "fail")


class RubricError(ValueError):
    """A rubric is malformed. Raised at load time, never at judgment time."""


class JudgeJevError(RuntimeError):
    """An operational failure: bad input, bad config, or an API problem."""


@dataclass
class Runtime:
    """Which build produced a result.

    Recorded because a verdict is only auditable against the code that reached it:
    `scripts/check-parity.sh` exists precisely because the two runtimes can drift,
    and a saved result that cannot say which one ran it cannot be checked against
    the other.
    """

    name: str
    version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    # The rubric version that produced this verdict. Without it `replay` re-routes
    # saved answers against whatever the rubric says today and reports the new
    # verdict as though it were the original judgment.
    rubric_version: str
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
    runtime: Runtime = field(default_factory=lambda: Runtime("python", RUNTIME_VERSION))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["usage"] = self.usage.to_dict()
        data["runtime"] = self.runtime.to_dict()
        return data


@dataclass
class Rubric:
    id: str
    version: str
    description: str
    model: str
    stakes: str
    confidence_floors: dict[str, float]
    state_filter: list[Any]
    questions: dict[str, dict[str, Any]]
    rules: tuple[RoutingRule, ...] = ()

    @property
    def confidence_floor(self) -> float:
        """The floor automatic verdicts must clear, from this rubric's stakes."""
        return float(self.confidence_floors.get(self.stakes, 0.0))

    def stage_of(self, answer_id: str) -> str | None:
        question = self.questions.get(answer_id)
        return question.get("stage") if question else None

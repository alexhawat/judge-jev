"""Optional Logfire (EU) tracing for the Python runtime.

Default off. Missing the `[tracing]` extra or token is a clean no-op.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from judge_jev.models import GateOutcome

EU_BASE_URL = "https://logfire-eu.pydantic.dev"
US_BASE_URL = "https://logfire-us.pydantic.dev"


@dataclass(frozen=True)
class TracingConfig:
    enabled: bool
    sink: str  # "logfire" when enabled

    @classmethod
    def from_cli(cls, *, tracing: bool, tracing_to: str | None) -> TracingConfig:
        if not tracing:
            return cls(enabled=False, sink="")
        sink = (tracing_to or "logfire").strip().lower()
        if sink != "logfire":
            raise ValueError(f"unsupported tracing sink {sink!r}; only 'logfire' is available")
        return cls(enabled=True, sink=sink)


def _resolve_region() -> str:
    region = os.environ.get("JUDGE_JEV_LOGFIRE_REGION", "eu").strip().lower()
    return region if region in ("eu", "us") else "eu"


def _resolve_base_url() -> str:
    return EU_BASE_URL if _resolve_region() == "eu" else US_BASE_URL


def _redact_text(value: str, *, limit: int = 120) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}…({len(value)} chars)"


def configure_tracing(config: TracingConfig) -> bool:
    """Configure Logfire when enabled. Returns True when a sink is active."""
    if not config.enabled:
        return False

    token = os.environ.get("JUDGE_JEV_LOGFIRE_TOKEN", "").strip()
    if not token:
        return False

    try:
        import logfire
    except ImportError:
        return False

    # The write token selects the project; configure has no project_name option.
    base_url = _resolve_base_url()
    logfire.configure(
        token=token,
        service_name="judge-jev",
        service_version=os.environ.get("JUDGE_JEV_VERSION", "0.1.0"),
        environment=os.environ.get("JUDGE_JEV_LOGFIRE_ENVIRONMENT", "local"),
        send_to_logfire=True,
        console=False,  # stdout is reserved for JudgmentResult JSON.
        advanced=logfire.AdvancedOptions(base_url=base_url),
    )
    return True


@contextmanager
def span_judge_run(
    *,
    rubric_id: str,
    rubric_version: str,
    model: str,
    mock: bool,
    active: bool,
) -> Iterator[Any]:
    if not active:
        yield None
        return
    import logfire

    with logfire.span(
        "judge.run",
        rubric_id=rubric_id,
        rubric_version=rubric_version,
        model=model,
        mock=mock,
    ) as run_span:
        yield run_span


@contextmanager
def span_gates(gate_outcomes: list[GateOutcome], *, active: bool) -> Iterator[Any]:
    if not active:
        yield None
        return
    import logfire

    summary = [
        {"gate_id": g.gate_id, "outcome": g.outcome, "reason": _redact_text(g.reason)}
        for g in gate_outcomes
    ]
    with logfire.span("gates", gate_count=len(summary), gates=summary) as gate_span:
        yield gate_span


@contextmanager
def span_system_one(
    *,
    question_count: int,
    model: str,
    mock: bool,
    state_bytes: int,
    active: bool,
) -> Iterator[Any]:
    if not active:
        yield None
        return
    import logfire

    with logfire.span(
        "jev.system_one",
        model=model,
        mock=mock,
        question_count=question_count,
        state_bytes=state_bytes,
    ) as jev_span:
        yield jev_span


@contextmanager
def span_route(
    *,
    verdict: str,
    confidence: float,
    deciding_answers: list[str],
    routing_reason: str,
    active: bool,
) -> Iterator[Any]:
    if not active:
        yield None
        return
    import logfire

    with logfire.span(
        "route",
        verdict=verdict,
        confidence=confidence,
        deciding_answers=deciding_answers,
        routing_reason=_redact_text(routing_reason, limit=200),
    ) as route_span:
        yield route_span

"""Retry policy, stated rather than inherited.

This runtime got retries for free from typesafe-sdk and the Rust runtime had none:
a 429 or a 503 was transparent here and a hard failure there, same rubric, same
input, outcome decided by which runtime the operator happened to install. In a hook
that gates work, that turns rate limiting into blocked developers.

`rust/src/retry.rs` now mirrors the SDK's defaults. This module spells the same
numbers out explicitly instead of letting them be implicit, for two reasons: one
environment variable should tune both runtimes, and a default that is written down
in one runtime and assumed in the other is exactly how the two drifted apart in the
first place. The values are typesafe-sdk 0.7.0's own
(`typesafe_sdk._core.retry.RetryPolicy`), so pinning them changes no behaviour
here — it just makes the Rust side something that can be checked against a written
contract rather than against a guess.

`timeout` is documented by the SDK as its total retry budget. Its Tenacity stop
policy checks elapsed time before another retry and refuses a delay that would
reach the budget. It does not preempt an in-flight SDK operation or shorten that
operation to the remaining budget. `PER_OPERATION_TIMEOUT` is the SDK's separate
per-operation default. Rust can enforce a stricter monotonic wall-clock deadline;
the distinction is documented instead of claiming guarantees the SDK does not make.
"""

from __future__ import annotations

import os

from typesafe_sdk import RetryPolicy

from judge_jev.models import JudgeJevError

MAX_RETRIES_ENV = "JUDGE_JEV_MAX_RETRIES"

# typesafe-sdk 0.7.0 defaults, pinned so both runtimes can be held to them.
DEFAULT_MAX_RETRIES = 2
BACKOFF_INITIAL = 0.5
BACKOFF_MAX = 5.0
BACKOFF_JITTER = 0.25
# Total retry budget per call, including the initial attempt and the delays.
TOTAL_TIMEOUT = 30.0
# The SDK's DEFAULT_TIMEOUT: per operation, not per call.
PER_OPERATION_TIMEOUT = 10.0

# 408 and 429 are the only 4xx worth retrying. A malformed request will not fix
# itself, and retrying it burns the budget before a real outage can use it.
RETRY_STATUSES = frozenset({408, 429, *range(500, 600)})


def max_retries() -> int:
    """Retries after the initial attempt, from the environment or the default."""
    raw = os.environ.get(MAX_RETRIES_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_MAX_RETRIES
    try:
        value = int(raw)
    except ValueError:
        raise JudgeJevError(
            f"{MAX_RETRIES_ENV} must be a non-negative integer, got {raw!r}"
        ) from None
    if value < 0:
        raise JudgeJevError(f"{MAX_RETRIES_ENV} must be a non-negative integer, got {raw!r}")
    return value


def retry_policy() -> RetryPolicy:
    """The policy both runtimes implement, built from the shared knob."""
    return RetryPolicy(
        max_retries=max_retries(),
        backoff_initial=BACKOFF_INITIAL,
        backoff_max=BACKOFF_MAX,
        backoff_jitter=BACKOFF_JITTER,
        http_statuses=set(RETRY_STATUSES),
        respect_retry_after=True,
        api_connection_error=True,
        api_timeout_error=True,
        timeout=TOTAL_TIMEOUT,
    )

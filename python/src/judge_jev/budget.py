"""The ~32k token budget a System One request has to fit in.

Neither runtime checked it. Oversized state was sent, the API rejected it after
the round trip, and the caller got an opaque error indistinguishable from a
transient outage — both surface as exit 10, so "too much state" and "the API is
down" looked the same and were handled the same. `agent-trajectory` is the rubric
most likely to hit it: a long agentic session runs past 150k characters without
trying.

So the estimate is made before the request is built, over the canonical form from
`canonical.py` — the exact bytes that would go on the wire, which is also the only
measure the two runtimes can agree on. The heuristic is bytes/4. It is not
accurate tokenization and does not need to be: it needs to be the same number in
both runtimes, and roughly right about a state that is an order of magnitude too
big. Bytes rather than characters, because Rust's `len()` counts bytes.

The estimate covers the questions as well as the state, since the budget is shared
between them, and a rubric whose questions alone are oversized should say so
rather than blame the state.

Over budget is exit 10, not a verdict: no judgment happened. The message names the
estimate, the ceiling, and the largest contributing key, because "too big" without
"and here is what is big" leaves the caller to bisect their own input.
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from judge_jev.canonical import CanonicalState, canonical_json, canonical_size
from judge_jev.models import JudgeJevError, Rubric

# Roughly what a System One call admits, shared between state and questions.
DEFAULT_TOKEN_BUDGET = 32_000

TOKEN_BUDGET_ENV = "JUDGE_JEV_TOKEN_BUDGET"

# Characters per token, near enough. See the module docstring.
BYTES_PER_TOKEN = 4


def estimate_tokens(size_bytes: int) -> int:
    """Round up, so a request is never estimated at zero tokens."""
    return (size_bytes + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN


def token_budget() -> int:
    """The ceiling, from the environment or the default."""
    raw = os.environ.get(TOKEN_BUDGET_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_TOKEN_BUDGET
    try:
        value = int(raw)
    except ValueError:
        raise JudgeJevError(
            f"{TOKEN_BUDGET_ENV} must be a positive integer, got {raw!r}"
        ) from None
    if value <= 0:
        raise JudgeJevError(f"{TOKEN_BUDGET_ENV} must be a positive integer, got {raw!r}")
    return value


def questions_payload(rubric: Rubric) -> dict[str, Any]:
    """The questions as they are sent: type, instructions, criteria.

    `stage` is routing metadata and never leaves the process, so it is not counted
    against a budget it does not spend.
    """
    payload: dict[str, Any] = {}
    for name, spec in rubric.questions.items():
        question: dict[str, Any] = {"type": spec["type"]}
        if spec.get("instructions") is not None:
            question["instructions"] = spec["instructions"]
        if spec.get("criteria") is not None:
            question["criteria"] = spec["criteria"]
        payload[name] = question
    return payload


def _largest_contributor(state: dict[str, Any], questions_bytes: int) -> tuple[str, int]:
    """The key worth shrinking first, with its estimate.

    `questions` competes as a contributor of its own: when a rubric's questions are
    what blows the budget, naming a state key would send the caller after the wrong
    thing.
    """
    sizes = {key: canonical_size(value) for key, value in state.items()}
    sizes["questions"] = questions_bytes
    # Sorted so the answer never depends on mapping order in either runtime.
    key = min(sizes, key=lambda name: (-sizes[name], name))
    return key, estimate_tokens(sizes[key])


def check_budget(state: CanonicalState, rubric: Rubric) -> int:
    """Estimate the request, log it, and refuse to send an oversized one.

    Returns the estimate. Runs in mock mode too: the guard is about the request
    that would be built, and catching an oversized trajectory in CI without paying
    for a call is most of the value.
    """
    questions_bytes = len(canonical_json(questions_payload(rubric)).encode("utf-8"))
    state_tokens = estimate_tokens(state.size)
    questions_tokens = estimate_tokens(questions_bytes)
    total = estimate_tokens(state.size + questions_bytes)
    budget = token_budget()

    # At INFO on every run, so it is visible long before it becomes a problem.
    logger.info(
        "token estimate {} of budget {} (state {}, questions {})",
        total,
        budget,
        state_tokens,
        questions_tokens,
    )

    if total > budget:
        key, key_tokens = _largest_contributor(state.value, questions_bytes)
        raise JudgeJevError(
            f"state is too large for one system_one call: estimated {total} tokens "
            f"against a {budget} token budget; largest contributor is '{key}' at "
            f"{key_tokens} tokens (raise {TOKEN_BUDGET_ENV} to override)"
        )
    return total

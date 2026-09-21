"""The ~32k token budget guard.

The estimate cases here are mirrored in `rust/src/budget.rs`; the two runtimes have
to produce the same number, or the guard fires on one and not the other and the
choice of runtime decides whether a judgment happens at all.
"""

from __future__ import annotations

import pytest

from judge_jev.budget import (
    DEFAULT_TOKEN_BUDGET,
    TOKEN_BUDGET_ENV,
    _largest_contributor,
    check_budget,
    estimate_tokens,
    questions_payload,
    token_budget,
)
from judge_jev.canonical import CanonicalState, canonical_json
from judge_jev.rubric import load_rubric
from judge_jev.typesafe_client import JudgeJevError


@pytest.mark.parametrize(
    ("size", "expected"), [(0, 0), (1, 1), (4, 1), (5, 2), (4000, 1000)]
)
def test_estimate_rounds_up(size, expected):
    assert estimate_tokens(size) == expected


def test_budget_defaults_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv(TOKEN_BUDGET_ENV, raising=False)
    assert token_budget() == DEFAULT_TOKEN_BUDGET
    monkeypatch.setenv(TOKEN_BUDGET_ENV, "1000")
    assert token_budget() == 1000


@pytest.mark.parametrize("raw", ["not-a-number", "0", "-5", "1.5"])
def test_a_malformed_budget_is_an_error_not_a_silent_default(monkeypatch, raw):
    # Silently falling back would hide the typo and send an oversized request.
    monkeypatch.setenv(TOKEN_BUDGET_ENV, raw)
    with pytest.raises(JudgeJevError):
        token_budget()


def test_largest_contributor_picks_the_biggest_key():
    key, _ = _largest_contributor({"goal": "short", "steps": "x" * 500}, 10)
    assert key == "steps"


def test_questions_can_be_the_largest_contributor():
    # A rubric whose questions alone blow the budget should say so rather than
    # send the caller looking through their state.
    key, _ = _largest_contributor({"goal": "short"}, 10_000)
    assert key == "questions"


def test_ties_break_by_name_so_both_runtimes_name_the_same_key():
    key, _ = _largest_contributor({"bbb": "xxxx", "aaa": "xxxx"}, 0)
    assert key == "aaa"


def test_questions_payload_leaves_out_stage():
    # `stage` is routing metadata; it never leaves the process, so it must not be
    # counted against a budget it does not spend.
    payload = questions_payload(load_rubric("assistant-reply"))
    assert payload
    assert all("stage" not in question for question in payload.values())
    assert all("type" in question for question in payload.values())


def test_an_oversized_state_is_refused_before_the_call():
    rubric = load_rubric("agent-trajectory")
    steps = [{"tool": "read", "input": f"file-{i}.py", "output": "x" * 400} for i in range(400)]
    state = CanonicalState.of({"goal": "refactor", "steps": steps, "final_output": "done"})
    with pytest.raises(JudgeJevError) as err:
        check_budget(state, rubric)
    message = str(err.value)
    # The three things a caller needs to act: how big, how big is allowed, and
    # what to shrink.
    assert "estimated" in message
    assert f"{DEFAULT_TOKEN_BUDGET} token budget" in message
    assert "largest contributor is 'steps'" in message
    assert TOKEN_BUDGET_ENV in message


def test_the_ceiling_can_be_raised(monkeypatch):
    rubric = load_rubric("agent-trajectory")
    steps = [{"tool": "read", "input": f"file-{i}.py", "output": "x" * 400} for i in range(400)]
    state = CanonicalState.of({"goal": "refactor", "steps": steps, "final_output": "done"})
    monkeypatch.setenv(TOKEN_BUDGET_ENV, "200000")
    assert check_budget(state, rubric) > DEFAULT_TOKEN_BUDGET


def test_a_normal_run_is_well_under_budget():
    rubric = load_rubric("assistant-reply")
    state = CanonicalState.of({"prompt": "hi", "reply": "hello"})
    assert check_budget(state, rubric) < DEFAULT_TOKEN_BUDGET


def test_the_estimate_counts_the_canonical_bytes_actually_sent():
    # Not the input file: the whole point of #10 is that they differ.
    rubric = load_rubric("assistant-reply")
    state = CanonicalState.of({"prompt": "hi", "reply": "hello"})
    questions_bytes = len(canonical_json(questions_payload(rubric)).encode())
    assert check_budget(state, rubric) == estimate_tokens(state.size + questions_bytes)

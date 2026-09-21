"""The retry policy, stated rather than inherited.

This runtime's retries come from typesafe-sdk. The point of pinning them here is
that `rust/src/retry.rs` has to mirror them, and a default written down on one side
and assumed on the other is how the two drifted apart in the first place. So these
tests assert the numbers the Rust module was built against, and would fail if an
SDK upgrade moved them underneath it.
"""

from __future__ import annotations

import pytest

from judge_jev.models import JudgeJevError
from judge_jev.retry import (
    BACKOFF_INITIAL,
    BACKOFF_JITTER,
    BACKOFF_MAX,
    DEFAULT_MAX_RETRIES,
    MAX_RETRIES_ENV,
    PER_OPERATION_TIMEOUT,
    RETRY_STATUSES,
    TOTAL_TIMEOUT,
    max_retries,
    retry_policy,
)


def test_the_sdk_defaults_are_what_rust_was_built_against():
    # If an SDK upgrade moves any of these, the Rust mirror is silently wrong and
    # this is the test that says so.
    from typesafe_sdk import RetryPolicy

    sdk = RetryPolicy()
    assert sdk.max_retries == DEFAULT_MAX_RETRIES
    assert sdk.backoff_initial == BACKOFF_INITIAL
    assert sdk.backoff_max == BACKOFF_MAX
    assert sdk.backoff_jitter == BACKOFF_JITTER
    assert sdk.timeout == TOTAL_TIMEOUT
    assert sdk.http_statuses == set(RETRY_STATUSES)
    assert sdk.respect_retry_after is True
    assert sdk.api_connection_error is True
    assert sdk.api_timeout_error is True


def test_the_per_operation_timeout_is_the_sdks_not_the_thirty_second_one():
    # rust/src/typesafe.rs used to set 30s for a whole request while claiming to
    # keep the two runtimes close; the SDK's per-operation default is 10s.
    from typesafe_sdk.constants import DEFAULT_TIMEOUT

    assert PER_OPERATION_TIMEOUT == DEFAULT_TIMEOUT
    assert PER_OPERATION_TIMEOUT != TOTAL_TIMEOUT


def test_only_408_429_and_5xx_are_retried():
    for status in (408, 429, 500, 502, 503, 504, 599):
        assert status in RETRY_STATUSES
    # A malformed request will not fix itself.
    for status in (400, 401, 403, 404, 409, 422):
        assert status not in RETRY_STATUSES


def test_max_retries_defaults_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv(MAX_RETRIES_ENV, raising=False)
    assert max_retries() == DEFAULT_MAX_RETRIES
    monkeypatch.setenv(MAX_RETRIES_ENV, "5")
    assert max_retries() == 5
    # Zero is meaningful: it disables retries.
    monkeypatch.setenv(MAX_RETRIES_ENV, "0")
    assert max_retries() == 0


@pytest.mark.parametrize("raw", ["not-a-number", "-1", "1.5"])
def test_a_malformed_max_retries_is_an_error(monkeypatch, raw):
    monkeypatch.setenv(MAX_RETRIES_ENV, raw)
    with pytest.raises(JudgeJevError):
        max_retries()


def test_the_policy_is_built_from_the_shared_knob(monkeypatch):
    # One environment variable tunes both runtimes; this runtime used to have no
    # way to set it at all.
    monkeypatch.setenv(MAX_RETRIES_ENV, "7")
    policy = retry_policy()
    assert policy.max_retries == 7
    assert policy.timeout == TOTAL_TIMEOUT
    assert policy.backoff_initial == BACKOFF_INITIAL


def test_the_client_is_given_the_policy_and_the_timeout(monkeypatch):
    """The policy has to actually reach the SDK, not just exist."""
    import judge_jev.typesafe_client as client_module

    seen: dict = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def __enter__(self):
            raise RuntimeError("stop here: the constructor is what is under test")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(client_module, "TypeSafeClient", FakeClient)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv(MAX_RETRIES_ENV, "3")

    from judge_jev.canonical import CanonicalState

    with pytest.raises(RuntimeError):
        client_module.LiveEngine().system_one(
            CanonicalState.of({"prompt": "hi"}), {}, "jev-1.13.0"
        )

    assert seen["retry"].max_retries == 3
    assert seen["timeout"] == PER_OPERATION_TIMEOUT

"""Canonical request serialization.

The number table below is duplicated, value for value, in the `formats_numbers_the
_way_python_does` test in `rust/src/canonical.rs`. That duplication is the point:
the two runtimes have no shared code, so the only way "both runtimes agree" can be
a fact rather than a hope is for each to assert the same expected bytes.
"""

from __future__ import annotations

import json
import math

import pytest

from judge_jev.canonical import CanonicalState, canonical_json, canonical_size

# Input JSON text -> the canonical bytes it must serialize to. Four of these are
# values serde_json writes differently, which is why rust/src/canonical.rs formats
# floats by hand instead of delegating.
NUMBERS = [
    ("0", "0"),
    ("1", "1"),
    ("-1", "-1"),
    ("3.0", "3.0"),
    ("100.0", "100.0"),
    ("2.5", "2.5"),
    ("0.1", "0.1"),
    ("-0.0", "-0.0"),
    ("0.30000000000000004", "0.30000000000000004"),
    ("1e-4", "0.0001"),
    ("1e-5", "1e-05"),
    ("1e-7", "1e-07"),
    ("1e15", "1000000000000000.0"),
    ("1e16", "1e+16"),
    ("1e20", "1e+20"),
    ("1e21", "1e+21"),
    ("1e30", "1e+30"),
    ("5e-324", "5e-324"),
    ("1.7976931348623157e308", "1.7976931348623157e+308"),
]


def test_sorts_keys_at_every_depth():
    # Nesting is where the divergence actually bit: the Rust runtime sorted
    # `{tool,input,output}` into `{input,output,tool}` inside every step.
    value = {"b": 1, "a": {"z": 1, "y": 2}, "c": [{"n": 1, "m": 2}]}
    assert canonical_json(value) == '{"a":{"y":2,"z":1},"b":1,"c":[{"m":2,"n":1}]}'


def test_uses_compact_separators():
    assert canonical_json({"a": [1, 2], "b": {"c": "d"}}) == '{"a":[1,2],"b":{"c":"d"}}'


def test_keeps_non_ascii_literal_and_escapes_controls():
    assert canonical_json({"k": 'é\x1f\n"\\'}) == '{"k":"é\\u001f\\n\\"\\\\"}'


@pytest.mark.parametrize(("raw", "expected"), NUMBERS, ids=[n[0] for n in NUMBERS])
def test_formats_numbers_the_way_rust_must(raw, expected):
    assert canonical_json(json.loads(raw)) == expected


def test_rejects_non_finite_numbers():
    # json.loads accepts NaN and Infinity where the Rust parser refuses them, so a
    # state carrying one would be judged on one runtime and rejected on the other.
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            canonical_json({"k": bad})


def test_size_is_bytes_not_characters():
    # Two characters, four bytes with the quotes: the estimate has to count what
    # was sent, and Rust's len() counts bytes.
    assert canonical_size("é") == 4


def test_canonical_state_carries_both_halves():
    state = CanonicalState.of({"b": 1, "a": 2})
    assert state.text == '{"a":2,"b":1}'
    assert state.value["b"] == 1
    assert state.size == 13


@pytest.mark.parametrize(
    "value",
    [-(2**63) - 1, 2**63, 2**64 - 1, 2**64, 184467440737095516170000000000000001],
)
def test_large_integers_remain_exact(value):
    assert canonical_json({"n": value}) == f'{{"n":{value}}}'

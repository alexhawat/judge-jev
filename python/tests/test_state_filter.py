"""Path-aware `state_filter`.

Every case here has a twin in `rust/src/state_filter.rs`, asserting the same
selection on the same input. The two runtimes share no code, so that duplication is
what makes "identical semantics" checkable rather than assumed; the byte-level
version of the same claim is the `wire-paths` case in scripts/check-parity.sh.
"""

from __future__ import annotations

import pytest

from judge_jev.canonical import canonical_json
from judge_jev.rubric import _parse_state_filter
from judge_jev.state_filter import StatePath, StatePathError, filter_state, parse_path
from judge_jev.typesafe_client import JudgeJevError

TRAJECTORY = {
    "goal": "find the readme",
    "steps": [
        {"tool": "glob", "input": "**/README.md", "output": "README.md"},
        {"tool": "read", "input": "README.md", "output": "# judge-jev"},
    ],
    "final_output": "done",
    "_mock_answers": {"screen.judgeable": {"type": "noul", "noul": 0.9}},
}


def paths(*specs: str) -> list[StatePath]:
    return [StatePath(path=s) for s in specs]


def test_a_plain_key_still_selects_the_whole_value():
    assert filter_state(TRAJECTORY, paths("goal")) == {"goal": "find the readme"}


def test_nested_key_leaves_its_siblings_behind():
    raw = {"ticket": {"subject": "card declined", "body": "x" * 4000}}
    assert filter_state(raw, paths("ticket.subject")) == {"ticket": {"subject": "card declined"}}


def test_projection_keeps_the_original_shape():
    # Not a bare list of tool names: `steps[].tool` in `instructions` has to keep
    # resolving against what the model is actually sent.
    assert filter_state(TRAJECTORY, paths("steps[].tool")) == {
        "steps": [{"tool": "glob"}, {"tool": "read"}]
    }


def test_two_projections_of_one_list_merge():
    assert filter_state(TRAJECTORY, paths("goal", "steps[].tool", "steps[].input")) == {
        "goal": "find the readme",
        "steps": [
            {"tool": "glob", "input": "**/README.md"},
            {"tool": "read", "input": "README.md"},
        ],
    }


def test_the_expensive_field_is_the_one_left_out():
    # The whole point of #10: `output` is the bulk of a trajectory and is billed.
    filtered = filter_state(TRAJECTORY, paths("steps[].tool"))
    assert "output" not in canonical_json(filtered)


def test_a_whole_list_can_still_be_selected():
    assert filter_state(TRAJECTORY, paths("steps[]")) == filter_state(TRAJECTORY, paths("steps"))


def test_an_element_missing_the_field_keeps_the_lists_length():
    # Dropping it would silently rewrite a trajectory that `locate.looping` counts.
    raw = {"steps": [{"tool": "glob"}, {"note": "no tool here"}, {"tool": "read"}]}
    assert filter_state(raw, paths("steps[].tool")) == {
        "steps": [{"tool": "glob"}, {}, {"tool": "read"}]
    }


def test_a_missing_path_is_dropped_and_the_run_continues():
    # fixtures/assistant-reply-skip.json depends on this: it omits `reply` so the
    # screen stage can short-circuit to `skip`.
    assert filter_state({"prompt": "hi"}, paths("prompt", "reply")) == {"prompt": "hi"}


def test_a_segment_applied_to_the_wrong_kind_of_value_is_missing():
    assert filter_state({"steps": {"not": "a list"}}, paths("steps[].tool")) == {}
    assert filter_state({"goal": "a string"}, paths("goal.nested")) == {}


def test_a_required_path_fails_the_run_instead_of_warning():
    with pytest.raises(JudgeJevError) as err:
        filter_state({"prompt": "hi"}, [StatePath("prompt"), StatePath("reply", required=True)])
    assert "required state_filter paths are missing from the input: reply" in str(err.value)


def test_required_is_opt_in():
    assert filter_state({"prompt": "hi"}, [StatePath("reply", required=False)]) == {}


def test_mock_answers_are_never_selectable():
    assert filter_state(TRAJECTORY, paths("_mock_answers")) == {}
    assert "_mock_answers" not in filter_state(TRAJECTORY, [])


def test_no_paths_means_everything_but_the_mock_block():
    assert set(filter_state(TRAJECTORY, [])) == {"goal", "steps", "final_output"}


@pytest.mark.parametrize("path", ["", "a..b", ".a", "a.", "a[b]", "a[][]", "a]b"])
def test_malformed_paths_are_rejected(path):
    with pytest.raises(StatePathError):
        parse_path(path)


def test_paths_are_parsed_at_rubric_load_not_mid_run():
    from judge_jev.models import RubricError

    with pytest.raises(RubricError):
        _parse_state_filter(["steps[oops]"])


def test_state_filter_entries_accept_both_forms():
    entries = _parse_state_filter(["goal", {"path": "steps[].tool", "required": True}])
    assert entries == [StatePath("goal", False), StatePath("steps[].tool", True)]


def test_a_flow_mapping_needs_the_path_quoted():
    # `[` and `]` are YAML flow indicators, so `{ path: steps[].tool }` is a YAML
    # parse error rather than a judge-jev one. Pinned here, and in the Rust twin,
    # because a rubric author writing the inline style will hit it and the error
    # they get comes from the YAML parser with no mention of state_filter.
    import yaml

    with pytest.raises(yaml.YAMLError):
        yaml.safe_load("- { path: steps[].tool }\n")
    assert yaml.safe_load("- path: steps[].tool\n  required: true\n") == [
        {"path": "steps[].tool", "required": True}
    ]


@pytest.mark.parametrize(
    "entry",
    [{"required": True}, {"path": "goal", "requried": True}, {"path": "goal", "required": "yes"}, 7],
)
def test_malformed_state_filter_entries_are_rejected(entry):
    from judge_jev.models import RubricError

    with pytest.raises(RubricError):
        _parse_state_filter([entry])

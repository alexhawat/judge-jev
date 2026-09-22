"""Routing, confidence gating, and funnel tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from judge_jev.cli import EXIT_ERROR, EXIT_REVIEW, EXIT_USAGE, main
from judge_jev.funnel import replay_judgment, run_judgment
from judge_jev.models import RubricError
from judge_jev.routing import decision_confidence, route_verdict
from judge_jev.rubric import load_rubric
from judge_jev.typesafe_client import JudgeJevError

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
SCHEMA = json.loads((REPO / "shared" / "schemas" / "judgment-result.schema.json").read_text())


def noul(value: float) -> dict:
    return {"type": "noul", "noul": value}


def score(value: float, confidence: float = 0.9) -> dict:
    return {
        "type": "score",
        "score": value,
        "confidence": confidence,
        "legend": {"0": "low", "1": "mid", "2": "ok", "3": "good", "4": "best"},
        "probabilities": {"0": 0.2, "1": 0.2, "2": 0.2, "3": 0.2, "4": 0.2},
    }


def choice(label: str, confidence: float = 0.9) -> dict:
    return {
        "type": "choice",
        "choice": label,
        "confidence": confidence,
        "probabilities": {label: confidence},
    }


def healthy_answers(**overrides: dict) -> dict[str, dict]:
    """A full answer set that routes to pass, so each test can perturb one thing."""
    answers = {
        "screen.judgeable": noul(0.95),
        "screen.injection": noul(0.05),
        "locate.hallucination_risk": noul(0.1),
        "locate.harmful": noul(0.02),
        "route.escalate": noul(0.05),
        "profile.intent": choice("answer"),
        "score.helpfulness": score(3.0),
        "score.coherence": score(3.0),
    }
    answers.update(overrides)
    return answers


# --- verdict table -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "overrides", "expected_verdict", "expected_stage"),
    [
        ("pass", {}, "pass", "score"),
        ("skip when not judgeable", {"screen.judgeable": noul(0.2)}, "skip", "screen"),
        ("escalate on injection", {"screen.injection": noul(0.9)}, "escalate", "screen"),
        ("escalate on harmful", {"locate.harmful": noul(0.85)}, "escalate", "locate"),
        ("escalate when flagged", {"route.escalate": noul(0.8)}, "escalate", "route"),
        (
            "fail on unhelpful refusal",
            {"profile.intent": choice("refusal"), "score.helpfulness": score(0.5)},
            "fail",
            "profile",
        ),
        (
            "review on hallucination risk",
            {"locate.hallucination_risk": noul(0.8), "score.helpfulness": score(3.0, confidence=0.5)},
            "review",
            "locate",
        ),
        ("review by default", {"score.helpfulness": score(1.0)}, "review", "route"),
    ],
)
def test_verdict_table(name, overrides, expected_verdict, expected_stage):
    rubric = load_rubric("assistant-reply")
    verdict, _reason, stage, _deciding, _confidence = route_verdict(rubric, healthy_answers(**overrides))
    assert verdict == expected_verdict, name
    assert stage == expected_stage, name


def test_every_verdict_is_reachable():
    """The exit-code contract lists five verdicts; the table above must cover all of them."""
    rubric = load_rubric("assistant-reply")
    cases = [
        {},
        {"screen.judgeable": noul(0.2)},
        {"screen.injection": noul(0.9)},
        {"profile.intent": choice("refusal"), "score.helpfulness": score(0.5)},
        {"score.helpfulness": score(1.0)},
    ]
    seen = {route_verdict(rubric, healthy_answers(**c))[0] for c in cases}
    assert seen == {"pass", "skip", "escalate", "fail", "review"}


# --- confidence ----------------------------------------------------------------


def test_confidence_comes_from_deciding_answers_only():
    """A confident but irrelevant answer must not inflate the verdict's confidence."""
    rubric = load_rubric("assistant-reply")
    answers = healthy_answers(
        # Very confident, but read by no matched rule.
        **{"locate.harmful": noul(0.0)},
    )
    answers["score.helpfulness"] = score(3.0, confidence=0.72)
    answers["score.coherence"] = score(3.0, confidence=0.80)

    verdict, _reason, _stage, deciding, confidence = route_verdict(rubric, answers)
    assert verdict == "pass"
    assert set(deciding) == {"score.helpfulness", "score.coherence"}
    # min of the deciding answers, not max over everything (which would be 1.0).
    assert confidence == pytest.approx(0.72)


def test_low_confidence_pass_is_downgraded_to_review():
    rubric = load_rubric("assistant-reply")
    answers = healthy_answers()
    answers["score.helpfulness"] = score(3.0, confidence=0.28)
    answers["score.coherence"] = score(3.0, confidence=0.30)

    verdict, reason, _stage, _deciding, confidence = route_verdict(rubric, answers)
    assert verdict == "review"
    assert confidence == pytest.approx(0.28)
    assert "below the read_only floor" in reason


def test_high_stakes_rubric_has_a_higher_floor():
    """The same confidence that passes a read_only rubric is held back on a write one."""
    assert load_rubric("assistant-reply").confidence_floor == 0.5
    trajectory = load_rubric("agent-trajectory")
    assert trajectory.confidence_floor == 0.7

    answers = {
        "screen.judgeable": noul(0.95),
        "screen.injection": noul(0.05),
        "locate.unauthorized_write": noul(0.05),
        "locate.looping": noul(0.05),
        "profile.task_type": choice("read"),
        "score.goal_alignment": score(3.5, confidence=0.6),
        "score.efficiency": score(3.0, confidence=0.6),
        "route.escalate": noul(0.05),
    }
    verdict, reason, _stage, _deciding, confidence = route_verdict(trajectory, answers)
    assert verdict == "review"
    assert confidence == pytest.approx(0.6)
    assert "below the write floor" in reason


def test_noul_confidence_is_distance_from_a_coin_flip():
    assert decision_confidence({"a": noul(0.5)}, ("a",)) == pytest.approx(0.0)
    assert decision_confidence({"a": noul(1.0)}, ("a",)) == pytest.approx(1.0)
    assert decision_confidence({"a": noul(0.0)}, ("a",)) == pytest.approx(1.0)


def test_default_rule_reports_no_confidence():
    rubric = load_rubric("assistant-reply")
    verdict, _reason, _stage, deciding, confidence = route_verdict(
        rubric, healthy_answers(**{"score.helpfulness": score(1.0)})
    )
    assert verdict == "review"
    assert deciding == []
    assert confidence == 0.0


# --- safety: a rule that cannot be evaluated must not be skipped ---------------


def test_missing_answer_escalates_instead_of_falling_through():
    """Dropping a security answer must not let a laxer rule decide the verdict."""
    rubric = load_rubric("assistant-reply")
    answers = healthy_answers()
    del answers["screen.injection"]

    verdict, reason, _stage, _deciding, _confidence = route_verdict(rubric, answers)
    assert verdict == "escalate"
    assert "screen.injection" in reason
    assert "missing" in reason.lower()


# --- rubric validation ---------------------------------------------------------


def test_shipped_rubrics_load():
    for rubric_id in ("assistant-reply", "agent-trajectory"):
        rubric = load_rubric(rubric_id)
        assert rubric.rules
        assert rubric.confidence_floor > 0


def test_every_question_is_read_by_some_rule():
    """A question that no rule reads costs tokens and changes nothing."""
    for rubric_id in ("assistant-reply", "agent-trajectory"):
        rubric = load_rubric(rubric_id)
        read = {c.answer for rule in rubric.rules for c in rule.conditions}
        unread = set(rubric.questions) - read
        assert not unread, f"{rubric_id} asks but never reads: {sorted(unread)}"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ({"answer": "nope.missing"}, "not a question"),
        ({"op": "~="}, "unknown op"),
        ({"field": "score"}, "not available on a 'noul' answer"),
    ],
)
def test_malformed_rule_is_rejected_at_load(tmp_path, monkeypatch, mutation, expected):
    import yaml

    source = yaml.safe_load((REPO / "shared" / "rubrics" / "assistant-reply.yaml").read_text())
    source["routing"]["rules"][0]["all"][0].update(mutation)
    target = tmp_path / "broken.yaml"
    target.write_text(yaml.safe_dump(source))
    monkeypatch.setattr("judge_jev.rubric.rubrics_dir", lambda: tmp_path)

    with pytest.raises(RubricError) as err:
        load_rubric("broken")
    assert expected in str(err.value)


def test_choice_value_must_be_a_declared_label(tmp_path, monkeypatch):
    import yaml

    source = yaml.safe_load((REPO / "shared" / "rubrics" / "assistant-reply.yaml").read_text())
    for rule in source["routing"]["rules"]:
        for condition in rule.get("all", []):
            if condition["field"] == "choice":
                condition["value"] = "not_a_label"
    target = tmp_path / "broken.yaml"
    target.write_text(yaml.safe_dump(source))
    monkeypatch.setattr("judge_jev.rubric.rubrics_dir", lambda: tmp_path)

    with pytest.raises(RubricError, match="is not a label"):
        load_rubric("broken")


# --- funnel over fixtures ------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "rubric_id", "expected"),
    [
        ("assistant-reply-pass.json", "assistant-reply", "pass"),
        ("assistant-reply-skip.json", "assistant-reply", "skip"),
        ("assistant-reply-injection.json", "assistant-reply", "escalate"),
        ("assistant-reply-escalate-flagged.json", "assistant-reply", "escalate"),
        ("assistant-reply-fail.json", "assistant-reply", "fail"),
        ("assistant-reply-low-confidence.json", "assistant-reply", "review"),
        ("agent-trajectory-pass.json", "agent-trajectory", "pass"),
    ],
)
def test_mock_funnel_verdicts(fixture, rubric_id, expected):
    result = run_judgment(rubric_id, FIXTURES / fixture, mock=True)
    assert result.verdict == expected
    assert result.model == "jev-1.13.0"
    assert result.mock is True
    assert 0.0 <= result.confidence <= 1.0


def test_injection_fixture_escalates_at_the_screen_stage():
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-injection.json", mock=True)
    assert result.verdict == "escalate"
    assert result.stage == "screen"
    assert result.deciding_answers == ["screen.injection"]
    assert "injection" in result.routing_reason.lower()


def test_mock_answers_block_never_reaches_the_model(monkeypatch):
    """`_mock_answers` is test scaffolding; it must be stripped from the state."""
    from judge_jev import typesafe_client

    seen: dict = {}
    original = typesafe_client.MockEngine.system_one

    def spy(self, state, questions, model):
        seen["state"] = state
        return original(self, state, questions, model)

    monkeypatch.setattr(typesafe_client.MockEngine, "system_one", spy)
    run_judgment("assistant-reply", FIXTURES / "assistant-reply-fail.json", mock=True)
    assert "_mock_answers" not in seen["state"].value
    assert "_comment" not in seen["state"].value
    # And not in the bytes a live call would have sent, which is the claim that
    # actually matters now that the request is built from exactly those bytes.
    assert "_mock_answers" not in seen["state"].text
    assert "_comment" not in seen["state"].text


# --- replay --------------------------------------------------------------------


def test_replay_preserves_answers_and_verdict():
    saved = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    replayed = replay_judgment(saved.to_dict())
    assert replayed.answers == saved.answers
    assert replayed.verdict == saved.verdict
    assert replayed.confidence == pytest.approx(saved.confidence)
    assert replayed.deciding_answers == saved.deciding_answers


def test_replay_recomputes_current_gates_under_stricter_rubric(monkeypatch):
    from judge_jev import funnel

    saved = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    saved_data = saved.to_dict()
    saved_data["rubric_version"] = "1.9.0"

    stricter = deepcopy(load_rubric("assistant-reply"))
    stricter.version = "2.1.0"
    stricter.confidence_floors[stricter.stakes] = 0.99
    monkeypatch.setattr(funnel, "load_rubric", lambda _rubric_id: stricter)

    replayed = replay_judgment(saved_data, allow_version_drift=True)
    gates = {gate.gate_id: gate for gate in replayed.deterministic_gates}

    assert replayed.verdict == "review"
    assert replayed.confidence_floor == pytest.approx(0.99)
    assert [gate.gate_id for gate in replayed.deterministic_gates] == [
        "state_projection",
        "token_budget",
        "injection_heuristic",
        "answer_completeness",
        "confidence_floor",
    ]
    assert all(
        gates[gate_id].reason.startswith("historical evidence from original run: ")
        for gate_id in ("state_projection", "token_budget", "injection_heuristic")
    )
    assert gates["answer_completeness"].outcome == "pass"
    assert gates["confidence_floor"].outcome == "fail"
    assert "below 0.99 floor (downgraded pass to review)" in gates["confidence_floor"].reason
    assert "meets 0.50 floor" not in gates["confidence_floor"].reason

    replayed_again = replay_judgment(replayed.to_dict())
    assert [gate.reason for gate in replayed_again.deterministic_gates[:3]] == [
        gate.reason for gate in replayed.deterministic_gates[:3]
    ]


def test_replay_recomputes_completeness_after_answers_change():
    saved = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    saved_data = saved.to_dict()
    del saved_data["answers"]["screen.injection"]

    replayed = replay_judgment(saved_data)
    gates = {gate.gate_id: gate for gate in replayed.deterministic_gates}

    assert replayed.verdict == "escalate"
    assert gates["answer_completeness"].outcome == "fail"
    assert "screen.injection" in gates["answer_completeness"].reason
    assert gates["confidence_floor"].outcome == "skip"


def test_replay_of_legacy_result_marks_unavailable_input_gates_as_skipped():
    saved = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    saved_data = saved.to_dict()
    saved_data.pop("deterministic_gates")
    saved_data.pop("state_projection")

    replayed = replay_judgment(saved_data)

    assert [gate.outcome for gate in replayed.deterministic_gates[:3]] == ["skip"] * 3
    assert all(
        "replay lacks raw state and historical" in gate.reason
        for gate in replayed.deterministic_gates[:3]
    )
    assert replayed.deterministic_gates[3].gate_id == "answer_completeness"
    assert replayed.deterministic_gates[3].outcome == "pass"

    replayed_again = replay_judgment(replayed.to_dict())
    assert replayed_again.deterministic_gates[:3] == replayed.deterministic_gates[:3]


# --- recorded live-shape payload ----------------------------------------------


def test_recorded_live_answers_route_and_validate():
    """Real API answers carry `legend`; routing and the schema must both accept them.

    The recorded fixture was saved before results carried provenance, so replaying it
    is refused by default — it cannot say which rubric version produced it, which is
    exactly the hole `rubric_version` closes. Routing it anyway is a deliberate act.
    """
    jsonschema = pytest.importorskip("jsonschema")
    saved = json.loads((FIXTURES / "recorded" / "assistant-reply-live.json").read_text())

    with pytest.raises(JudgeJevError, match="carries no rubric_version"):
        replay_judgment(saved)

    result = replay_judgment(saved, allow_version_drift=True)

    assert "legend" in result.answers["score.helpfulness"]
    jsonschema.validate(result.to_dict(), SCHEMA)

    # The scores clear the pass thresholds, but the model was near a coin flip on
    # both, so the floor holds it back. This is the case that shipped as pass/0.96.
    assert result.verdict == "review"
    assert result.confidence == pytest.approx(0.28)
    assert set(result.deciding_answers) == {"score.helpfulness", "score.coherence"}


def test_mock_result_validates_against_schema():
    jsonschema = pytest.importorskip("jsonschema")
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    jsonschema.validate(result.to_dict(), SCHEMA)


# --- CLI error contract --------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--rubric", "assistant-reply", "--input", "does-not-exist.json", "--mock"],
        ["run", "--rubric", "no-such-rubric", "--input", "fixtures/assistant-reply-pass.json", "--mock"],
        ["replay", "--input", "does-not-exist.json"],
    ],
)
def test_operational_failures_exit_distinctly_from_fail(argv, capsys):
    """Exit 1 means the verdict was 'fail'. A crash must not look like one."""
    code = main(argv)
    assert code == EXIT_ERROR
    assert code != 1
    err = capsys.readouterr().err
    assert "judge-jev:" in err
    assert "Traceback" not in err


# --- CLI usage contract --------------------------------------------------------


@pytest.mark.parametrize(
    "argv,message",
    [
        # A typo used to be dropped on the floor. In the Rust runtime that silently
        # cleared --mock and billed a live call; here argparse exited 2, which IS
        # the review verdict.
        (["run", "--rubric", "assistant-reply", "--input", "x.json", "--mok"],
         "unrecognized flag: --mok"),
        (["run", "--rubric", "assistant-reply", "--rubric", "agent-trajectory", "--input", "x.json"],
         "--rubric given more than once"),
        (["run", "--input", "x.json", "--mock", "--rubric"], "--rubric needs a value"),
        (["run", "--input", "x.json", "--mock"], "--rubric is required"),
        (["run", "--rubric", "assistant-reply", "--mock"], "--input is required"),
        ([], "a command is required"),
        (["bogus"], "unknown command: bogus"),
        (["rubric"], "rubric needs a subcommand: list or show"),
        (["rubric", "bogus"], "unknown rubric command: bogus"),
        (["replay", "--input", "x.json", "--mock"], "unrecognized flag: --mock"),
        (["run", "--rubric", "assistant-reply", "--input", "x.json", "extra"],
         "unexpected argument: extra"),
    ],
)
def test_usage_errors_exit_11_and_say_why(argv, message, capsys):
    """11, never 2. 2 is EXIT_REVIEW and a hook would queue an unjudged action."""
    code = main(argv)
    assert code == EXIT_USAGE
    assert code != EXIT_REVIEW
    assert message in capsys.readouterr().err


def test_help_still_exits_zero(capsys):
    assert main(["--help"]) == 0
    assert "judge-jev" in capsys.readouterr().out


# --- stdin ---------------------------------------------------------------------


def test_input_dash_reads_stdin_for_run_and_replay(monkeypatch, capsys):
    """`run --input - | replay --input -` is the composition this exists for."""
    import io
    import sys as _sys

    monkeypatch.setattr(_sys, "stdin", io.StringIO((FIXTURES / "assistant-reply-pass.json").read_text()))
    assert main(["run", "--rubric", "assistant-reply", "--input", "-", "--mock"]) == 0
    judged = capsys.readouterr().out

    monkeypatch.setattr(_sys, "stdin", io.StringIO(judged))
    assert main(["replay", "--input", "-"]) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["verdict"] == json.loads(judged)["verdict"]
    assert replayed["answers"] == json.loads(judged)["answers"]


def test_dash_is_stdin_not_a_file_under_the_repo_root():
    from judge_jev.funnel import resolve_input_path

    assert str(resolve_input_path(Path("-"))) == "-"


def test_missing_input_message_is_the_shared_wording(capsys):
    """Both runtimes emit this string verbatim; check-parity.sh compares them."""
    assert main(["run", "--rubric", "assistant-reply", "--input", "nope.json", "--mock"]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "cannot read input nope.json: No such file or directory (os error 2)" in err


def test_result_records_the_rubric_version_and_the_runtime_that_ran_it():
    """A verdict is only auditable against the rules and the build that produced it."""
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    rubric = load_rubric("assistant-reply")

    assert result.rubric_version == rubric.version
    assert result.runtime.name == "python"
    assert result.runtime.version  # whatever it is, it must not be empty

    data = result.to_dict()
    assert data["runtime"] == {"name": "python", "version": result.runtime.version}


def test_mock_records_the_requested_model_since_nothing_answered():
    """`model` is the model that answered; under --mock that is the one we asked for."""
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True)
    assert result.model == load_rubric("assistant-reply").model


def test_model_records_what_answered_not_what_was_asked_for(monkeypatch):
    """A live API may resolve an alias; the result must name what actually judged.

    This runtime used to record the requested model and drop `response.model` after
    logging it, so the same judgment was attributed to two different models
    depending on which runtime ran it.
    """
    from judge_jev import funnel

    class AliasResolvingEngine:
        def system_one(self, state, questions, model):
            from judge_jev.models import Usage

            answers = {
                name: {"type": "noul", "noul": 0.2}
                for name, spec in load_rubric("assistant-reply").questions.items()
                if spec["type"] == "noul"
            }
            return answers, Usage(1, 2), "req-1", "jev-1.13.0-20260920"

    monkeypatch.setattr(funnel, "get_engine", lambda *_a, **_k: AliasResolvingEngine())
    result = run_judgment("assistant-reply", FIXTURES / "assistant-reply-pass.json")
    assert result.model == "jev-1.13.0-20260920"


def test_replay_refuses_a_rubric_version_it_was_not_judged_under(tmp_path):
    """The whole point of replay is re-deriving a verdict; drifted rules cannot."""
    saved = run_judgment(
        "assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True
    ).to_dict()
    saved["rubric_version"] = "0.0.0-not-the-one-on-disk"

    with pytest.raises(JudgeJevError, match="0.0.0-not-the-one-on-disk"):
        replay_judgment(saved)

    routed = replay_judgment(saved, allow_version_drift=True)
    # The result records the version it was ROUTED under, so the reason carries the
    # original — otherwise the drift would leave no trace at all.
    assert routed.rubric_version == load_rubric("assistant-reply").version
    assert "0.0.0-not-the-one-on-disk" in routed.routing_reason


def test_replay_drift_is_exit_10_not_a_verdict(tmp_path, capsys):
    """A refused replay did not judge anything, so it must not look like one."""
    saved = run_judgment(
        "assistant-reply", FIXTURES / "assistant-reply-pass.json", mock=True
    ).to_dict()
    saved["rubric_version"] = "0.0.0-not-the-one-on-disk"
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(saved), encoding="utf-8")

    assert main(["replay", "--input", str(path)]) == EXIT_ERROR
    assert main(["replay", "--input", str(path), "--allow-version-drift"]) == 0


def test_version_flag_names_the_runtime(capsys):
    from judge_jev.models import RUNTIME_VERSION

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"judge-jev {RUNTIME_VERSION} (python)"

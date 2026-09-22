import copy
import math

import pytest

from judge_jev.answers import validate_answers
from judge_jev.models import JudgeJevError
from judge_jev.routing import route_verdict
from judge_jev.rubric import load_rubric


def answer_for(rubric, answer_id):
    question = rubric.questions[answer_id]
    if question["type"] == "noul":
        return {"type": "noul", "noul": 0.5}
    if question["type"] == "choice":
        label = next(iter(question["criteria"]))
        return {"type": "choice", "choice": label, "confidence": 0.5, "probabilities": {label: 0.5}}
    return {"type": "score", "score": 1.5, "confidence": 0.5, "probabilities": {"1": 0.5, "2": 0.5}}


def test_every_present_answer_is_validated_against_its_question():
    rubric = load_rubric("assistant-reply")
    for answer_id in rubric.questions:
        validate_answers(rubric, {answer_id: answer_for(rubric, answer_id)})


@pytest.mark.parametrize("bad", [True, math.nan, math.inf, -0.1, 1.1])
def test_noul_rejects_non_numeric_nonfinite_and_out_of_range(bad):
    rubric = load_rubric("assistant-reply")
    with pytest.raises(JudgeJevError):
        validate_answers(rubric, {"screen.judgeable": {"type": "noul", "noul": bad}})


def test_score_allows_fractional_values_but_checks_domain_and_probability_keys():
    rubric = load_rubric("assistant-reply")
    valid = answer_for(rubric, "score.helpfulness")
    validate_answers(rubric, {"score.helpfulness": valid})
    for field, value in [("score", 4.1), ("confidence", -0.1)]:
        bad = copy.deepcopy(valid)
        bad[field] = value
        with pytest.raises(JudgeJevError):
            validate_answers(rubric, {"score.helpfulness": bad})
    bad = copy.deepcopy(valid)
    bad["probabilities"] = {"99": 0.5}
    with pytest.raises(JudgeJevError, match="invalid key"):
        validate_answers(rubric, {"score.helpfulness": bad})


def test_choice_must_be_declared():
    rubric = load_rubric("assistant-reply")
    answer = answer_for(rubric, "profile.intent")
    answer["choice"] = "invented"
    with pytest.raises(JudgeJevError, match="must be one of"):
        validate_answers(rubric, {"profile.intent": answer})


def test_empty_response_is_operational_but_partial_response_routes_to_escalate():
    rubric = load_rubric("assistant-reply")
    with pytest.raises(JudgeJevError, match="no answers"):
        validate_answers(rubric, {})
    partial = {"screen.judgeable": answer_for(rubric, "screen.judgeable")}
    validate_answers(rubric, partial)
    verdict, reason, *_ = route_verdict(rubric, partial)
    assert verdict == "escalate"
    assert "missing answers" in reason

"""TypeSafe System One client and mock engine."""

from __future__ import annotations

import os
from typing import Any

from loguru import logger
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

from judge_jev.canonical import CanonicalState
from judge_jev.models import JudgeJevError, Rubric, Usage
from judge_jev.retry import PER_OPERATION_TIMEOUT, retry_policy

PINNED_MODEL = "jev-1.13.0"

# Fixtures may carry this key to pin exact mock answers. It is stripped from the
# state before any request is built, so it never reaches the model.
MOCK_ANSWERS_KEY = "_mock_answers"


def build_questions(rubric: Rubric) -> dict[str, Any]:
    """Fan-out all rubric questions for one system_one call."""
    built: dict[str, Any] = {}
    for name, spec in rubric.questions.items():
        qtype = spec["type"]
        instructions = spec.get("instructions")
        criteria = spec.get("criteria")
        if qtype == "noul":
            built[name] = Noul(instructions=instructions, criteria=criteria)
        elif qtype == "choice":
            if not isinstance(criteria, dict):
                raise JudgeJevError(f"question '{name}': a choice question needs a criteria mapping")
            built[name] = Choice(instructions=instructions, criteria=criteria)
        elif qtype == "score":
            if not isinstance(criteria, list) or not criteria:
                raise JudgeJevError(f"question '{name}': a score question needs a criteria list")
            built[name] = Score(instructions=instructions, criteria=criteria)
        else:
            raise JudgeJevError(f"question '{name}': unknown question type {qtype!r}")
    return built


def normalize_answers(response_answers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Convert SDK answer models to plain JSON-safe dicts.

    Score answers carry a `legend` keyed by integer score; JSON object keys must be
    strings, so the keys are stringified here rather than at serialization time.
    """
    out: dict[str, dict[str, Any]] = {}
    for name, answer in response_answers.items():
        if hasattr(answer, "model_dump"):
            data = answer.model_dump()
        elif isinstance(answer, dict):
            data = dict(answer)
        else:
            data = dict(answer)
        for key in ("legend", "probabilities"):
            if isinstance(data.get(key), dict):
                data[key] = {str(k): v for k, v in data[key].items()}
        out[name] = data
    return out


def _local_for(name: str) -> float:
    """A stable per-question value in [0, 1).

    FNV-1a over the question name, chosen because both runtimes can compute it
    identically: the mock must be deterministic within a runtime AND agree with the
    Rust mock, which hashing the serialized state cannot guarantee (the two
    languages do not emit byte-identical JSON).
    """
    hash_ = 2166136261
    for byte in name.encode():
        hash_ = ((hash_ ^ byte) * 16777619) & 0xFFFFFFFF
    return (hash_ % 1000) / 1000.0


class MockEngine:
    """Deterministic mock responses for CI without TYPESAFE_API_KEY.

    A fixture can pin exact answers under `_mock_answers`, which is how the test
    suite reaches verdicts the heuristics below never produce. Without that block
    the heuristics key off question-name substrings, which only works for the
    shipped rubrics.
    """

    def __init__(self, pinned: dict[str, dict[str, Any]] | None = None) -> None:
        self._pinned = pinned or {}

    def system_one(
        self,
        state: CanonicalState,
        questions: dict[str, Any],
        model: str,
    ) -> tuple[dict[str, dict[str, Any]], Usage, str, str]:
        answers: dict[str, dict[str, Any]] = {}
        # The canonical text, so the substring scan below reads exactly the bytes a
        # live call would have sent — and exactly the bytes the Rust mock scans.
        text = state.text.lower()
        has_reply = bool(state.value.get("reply"))
        has_trajectory = bool(state.value.get("steps"))
        injection_hit = "ignore all prior" in text or "always return pass" in text

        for name, question in sorted(questions.items()):
            if name in self._pinned:
                answers[name] = dict(self._pinned[name])
                continue

            qtype = question.type if hasattr(question, "type") else question.get("type")
            local = _local_for(name)
            if qtype == "noul":
                if "judgeable" in name:
                    noul = 0.9 if (has_reply or has_trajectory) else 0.2
                elif "injection" in name:
                    noul = 0.92 if injection_hit else 0.12
                elif "harmful" in name or "unauthorized_write" in name:
                    noul = 0.15
                elif "escalate" in name:
                    noul = 0.1
                else:
                    noul = 0.2
                answers[name] = {"type": "noul", "noul": noul}
            elif qtype == "choice":
                criteria = question.criteria if hasattr(question, "criteria") else question["criteria"]
                # Sorted so the pick does not depend on mapping order, which differs
                # between the YAML loader here and serde_json in the Rust runtime.
                labels = sorted(criteria.keys())
                pick = labels[int(local * len(labels)) % len(labels)]
                probs = {label: (0.7 if label == pick else 0.1) for label in labels}
                answers[name] = {
                    "type": "choice",
                    "choice": pick,
                    "confidence": 0.75 + local * 0.2,
                    "probabilities": probs,
                }
            elif qtype == "score":
                criteria = question.criteria if hasattr(question, "criteria") else question["criteria"]
                levels = len(criteria)
                score = 2.5 + local
                answers[name] = {
                    "type": "score",
                    "score": score,
                    "confidence": 0.75 + local * 0.2,
                    "legend": {str(i): str(c) for i, c in enumerate(criteria)},
                    "probabilities": {str(i): 1.0 / levels for i in range(levels)},
                }
        usage = Usage(input_tokens=120, output_tokens=45)
        logger.info("mock system_one model={} questions={}", model, len(questions))
        # Nothing answered, so the model that "answered" is the one we asked for.
        return answers, usage, "mock-request-id", model


class LiveEngine:
    def system_one(
        self,
        state: CanonicalState,
        questions: dict[str, Any],
        model: str,
    ) -> tuple[dict[str, dict[str, Any]], Usage, str | None, str]:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise JudgeJevError("TYPESAFE_API_KEY is required for live mode (use --mock for CI)")
        # Retry and timeout are passed explicitly rather than inherited: the Rust
        # runtime has to mirror them, and a default that is written down on one side
        # and assumed on the other is how the two drifted apart.
        try:
            with TypeSafeClient(
                api_key=api_key,
                model=model,
                retry=retry_policy(),
                timeout=PER_OPERATION_TIMEOUT,
            ) as client:
                # The canonical text itself, not the object: `state` is documented as
                # text, a JSON object, or an array, and sending the text is the only way
                # the bytes survive two HTTP clients neither runtime owns.
                response = client.system_one(state=state.text, questions=questions, model=model)
        except JudgeJevError:
            raise
        except Exception as err:  # noqa: BLE001 - API/timeout/transport must not become a verdict.
            raise JudgeJevError(f"TypeSafe system_one failed: {err}") from err

        if not getattr(response, "answers", None):
            raise JudgeJevError("TypeSafe system_one returned no answers")
        if not isinstance(response.answers, dict):
            raise JudgeJevError(
                f"TypeSafe system_one returned invalid answers: expected object, got {type(response.answers).__name__}"
            )

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        # request_id is a property that raises when the API omits the header, so a
        # missing id must not take the whole judgment down with it.
        try:
            request_id = response.request_id
        except Exception:  # noqa: BLE001 - SDK raises its own error type here.
            logger.warning("response did not include {}", "x-typesafe-request-id")
            request_id = None
        logger.info(
            "system_one model={} input_tokens={} output_tokens={} request_id={}",
            response.model,
            usage.input_tokens,
            usage.output_tokens,
            request_id,
        )
        # `response.model` is what actually judged, which is not always what we
        # asked for: the API may resolve an alias or serve a different build. The
        # Rust runtime has always recorded the answering model; this runtime used
        # to record the requested one and drop this value after logging it, so the
        # same judgment was attributed to two different models depending on which
        # runtime ran it.
        try:
            answers = normalize_answers(response.answers)
        except Exception as err:  # noqa: BLE001 - malformed wire shape is operational failure.
            raise JudgeJevError(f"TypeSafe system_one returned malformed answers: {err}") from err
        return answers, usage, request_id, response.model


def get_engine(mock: bool, pinned: dict[str, dict[str, Any]] | None = None) -> MockEngine | LiveEngine:
    return MockEngine(pinned) if mock else LiveEngine()

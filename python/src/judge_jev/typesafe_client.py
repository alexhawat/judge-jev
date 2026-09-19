"""TypeSafe System One client and mock engine."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from loguru import logger
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

from judge_jev.models import Rubric, Usage

PINNED_MODEL = "jev-1.13.0"


def filter_state(raw: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    if not keys:
        return raw
    return {k: raw[k] for k in keys if k in raw}


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
            built[name] = Choice(instructions=instructions, criteria=criteria)
        elif qtype == "score":
            built[name] = Score(instructions=instructions, criteria=criteria)
        else:
            raise ValueError(f"Unknown question type: {qtype}")
    return built


def normalize_answers(response_answers: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, answer in response_answers.items():
        if hasattr(answer, "model_dump"):
            out[name] = answer.model_dump()
        elif isinstance(answer, dict):
            out[name] = answer
        else:
            out[name] = dict(answer)
    return out


class MockEngine:
    """Deterministic mock responses for CI without TYPESAFE_API_KEY."""

    def system_one(
        self,
        state: dict[str, Any] | str,
        questions: dict[str, Any],
        model: str,
    ) -> tuple[dict[str, dict[str, Any]], Usage, str]:
        blob = json.dumps({"state": state, "questions": sorted(questions)}, sort_keys=True)
        seed = int(hashlib.sha256(blob.encode()).hexdigest()[:8], 16)

        answers: dict[str, dict[str, Any]] = {}
        text = json.dumps(state).lower() if isinstance(state, dict) else str(state).lower()
        has_reply = isinstance(state, dict) and bool(state.get("reply"))
        has_trajectory = isinstance(state, dict) and bool(state.get("steps"))
        injection_hit = "ignore all prior" in text or "always return pass" in text
        for idx, (name, question) in enumerate(sorted(questions.items())):
            qtype = question.type if hasattr(question, "type") else question.get("type")
            local = (seed + idx * 17) % 1000 / 1000.0
            if qtype == "noul":
                if "judgeable" in name:
                    noul = 0.9 if (has_reply or has_trajectory) else 0.2
                elif "injection" in name:
                    noul = 0.92 if injection_hit else 0.12
                elif "harmful" in name or "unauthorized_write" in name:
                    noul = 0.15
                else:
                    noul = 0.55
                answers[name] = {"type": "noul", "noul": noul}
            elif qtype == "choice":
                criteria = question.criteria if hasattr(question, "criteria") else question["criteria"]
                labels = list(criteria.keys())
                pick = labels[int(local * len(labels)) % len(labels)]
                probs = {label: (0.7 if label == pick else 0.1) for label in labels}
                answers[name] = {
                    "type": "choice",
                    "choice": pick,
                    "confidence": 0.75 + local * 0.2,
                    "probabilities": probs,
                }
            elif qtype == "score":
                score = 2.5 + local
                answers[name] = {
                    "type": "score",
                    "score": score,
                    "confidence": 0.7 + local * 0.25,
                    "probabilities": {"2": 0.2, "3": 0.5, "4": 0.3},
                }
        usage = Usage(input_tokens=120, output_tokens=45)
        logger.info("mock system_one model={} questions={}", model, len(questions))
        return answers, usage, "mock-request-id"


class LiveEngine:
    def system_one(
        self,
        state: dict[str, Any] | str,
        questions: dict[str, Any],
        model: str,
    ) -> tuple[dict[str, dict[str, Any]], Usage, str | None]:
        api_key = os.environ.get("TYPESAFE_API_KEY")
        if not api_key:
            raise RuntimeError("TYPESAFE_API_KEY is required for live mode (use --mock for CI)")
        with TypeSafeClient(api_key=api_key, model=model) as client:
            response = client.system_one(state=state, questions=questions, model=model)
            usage = Usage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            logger.info(
                "system_one model={} input_tokens={} output_tokens={} request_id={}",
                response.model,
                usage.input_tokens,
                usage.output_tokens,
                response.request_id,
            )
            return normalize_answers(response.answers), usage, response.request_id


def get_engine(mock: bool) -> MockEngine | LiveEngine:
    return MockEngine() if mock else LiveEngine()

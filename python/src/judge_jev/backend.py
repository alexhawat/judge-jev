"""Provider boundary for one batched System One operation."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from judge_jev.canonical import CanonicalState
from judge_jev.models import JudgeJevError, Usage
from judge_jev.retry import BACKOFF_INITIAL, BACKOFF_MAX, PER_OPERATION_TIMEOUT, TOTAL_TIMEOUT, max_retries
from judge_jev.typesafe_client import LiveEngine, MockEngine, normalize_answers

BACKEND_IDS = ("typesafe", "cloudflare", "replay")
CLOUDFLARE_MODEL = "typesafe/jev"
CLOUDFLARE_URL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run"


@dataclass(frozen=True)
class BackendCapabilities:
    question_types: tuple[str, ...]
    uncertainty: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


JEV_CAPABILITIES = BackendCapabilities(
    question_types=("noul", "choice", "score"),
    uncertainty={
        "noul": "distance_from_half",
        "choice": "distribution_confidence",
        "score": "distribution_confidence",
    },
)


@dataclass
class BackendResponse:
    answers: dict[str, dict[str, Any]]
    usage: Usage
    request_id: str | None
    requested_model: str
    resolved_model: str
    provenance: str


class Backend(Protocol):
    id: str
    capabilities: BackendCapabilities

    def system_one(
        self, state: CanonicalState, questions: dict[str, Any], model: str
    ) -> BackendResponse: ...


def resolve_backend(explicit: str | None, *, mock: bool, environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    env_value = env.get("JUDGE_JEV_BACKEND")
    selected = explicit or env_value or ("replay" if mock else "typesafe")
    if selected not in BACKEND_IDS:
        raise ValueError(f"unknown backend {selected!r}; expected one of {', '.join(BACKEND_IDS)}")
    if mock and selected != "replay":
        raise ValueError("--mock is the canned replay backend and cannot be combined with a live backend")
    return selected


def validate_capabilities(capabilities: BackendCapabilities, questions: dict[str, Any]) -> None:
    supported = set(capabilities.question_types)
    for name, question in questions.items():
        qtype = question.type if hasattr(question, "type") else question.get("type")
        if qtype not in supported:
            raise JudgeJevError(f"backend does not support {qtype!r} question {name!r}")


class TypeSafeBackend:
    id = "typesafe"
    capabilities = JEV_CAPABILITIES

    def __init__(self) -> None:
        self._engine = LiveEngine()

    def system_one(self, state: CanonicalState, questions: dict[str, Any], model: str) -> BackendResponse:
        answers, usage, request_id, resolved_model = self._engine.system_one(state, questions, model)
        return BackendResponse(answers, usage, request_id, model, resolved_model, "live_model")


class ReplayBackend:
    id = "replay"
    capabilities = JEV_CAPABILITIES

    def __init__(
        self,
        pinned: dict[str, dict[str, Any]] | None = None,
        recorded: dict[str, Any] | None = None,
    ) -> None:
        self.recorded = recorded
        self._engine = MockEngine(pinned)

    def system_one(self, state: CanonicalState, questions: dict[str, Any], model: str) -> BackendResponse:
        if self.recorded is not None:
            answers = self.recorded.get("answers")
            if not isinstance(answers, dict) or not answers:
                raise JudgeJevError("recorded replay needs non-empty answers")
            saved_version = self.recorded.get("rubric_version")
            if saved_version is None:
                raise JudgeJevError("recorded replay needs rubric_version provenance")
            usage_data = self.recorded.get("usage") or {}
            usage = Usage(usage_data.get("input_tokens"), usage_data.get("output_tokens"))
            resolved = str(self.recorded.get("resolved_model") or self.recorded.get("model") or model)
            return BackendResponse(
                normalize_answers(answers),
                usage,
                self.recorded.get("request_id"),
                str(self.recorded.get("requested_model") or model),
                resolved,
                "recorded_model",
            )
        answers, usage, request_id, resolved = self._engine.system_one(state, questions, model)
        return BackendResponse(answers, usage, request_id, model, resolved, "canned_demo")


def _question_json(question: Any) -> dict[str, Any]:
    if hasattr(question, "model_dump"):
        data = question.model_dump(exclude_none=True)
    elif isinstance(question, dict):
        data = {k: v for k, v in question.items() if v is not None}
    else:
        data = dict(question)
    return data


class CloudflareBackend:
    """Cloudflare Workers AI contract documented for ``typesafe/jev``.

    The provider accepts an alias rather than the rubric's version pin. A response
    is usable only when its resolved model equals that pin.
    """

    id = "cloudflare"
    capabilities = JEV_CAPABILITIES

    def __init__(
        self,
        *,
        opener: Any = None,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> None:
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.monotonic = monotonic

    def system_one(self, state: CanonicalState, questions: dict[str, Any], model: str) -> BackendResponse:
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        if not token or not account:
            raise JudgeJevError("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required")
        url = os.environ.get("JUDGE_JEV_CLOUDFLARE_URL") or CLOUDFLARE_URL.format(account=account)
        payload = {
            "model": CLOUDFLARE_MODEL,
            "input": {
                "state": state.text,
                "questions": {name: _question_json(q) for name, q in questions.items()},
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        last: Exception | None = None
        response_body: bytes | None = None
        started = self.monotonic()
        attempts = max_retries() + 1
        for attempt in range(attempts):
            try:
                remaining = TOTAL_TIMEOUT - (self.monotonic() - started)
                if remaining <= 0:
                    break
                with self.opener(request, timeout=min(PER_OPERATION_TIMEOUT, remaining)) as response:
                    response_body = response.read()
                    request_id = response.headers.get("cf-ray")
                break
            except urllib.error.HTTPError as err:
                error_body = err.read().decode("utf-8", "replace")
                last = JudgeJevError(f"Cloudflare API error {err.code}: {error_body}")
                if err.code not in (408, 429) and err.code < 500:
                    raise last
                retry_after = err.headers.get("retry-after") if err.headers else None
            except (OSError, TimeoutError) as err:
                last = err
                retry_after = None
            if attempt < attempts - 1:
                fallback = min(BACKOFF_MAX, BACKOFF_INITIAL * (2**attempt))
                try:
                    parsed_after = float(retry_after) if retry_after is not None else fallback
                except (TypeError, ValueError):
                    parsed_after = fallback
                delay = parsed_after if math.isfinite(parsed_after) and parsed_after >= 0 else fallback
                if self.monotonic() - started + delay >= TOTAL_TIMEOUT:
                    break
                self.sleep(delay)
        else:
            raise JudgeJevError(f"Cloudflare system_one failed: {last}") from last
        if response_body is None:
            raise JudgeJevError(f"Cloudflare system_one failed: {last}") from last
        try:
            raw = json.loads(response_body)
        except json.JSONDecodeError as err:
            # A successful malformed envelope is a provider contract failure, not
            # a transient transport event; retrying it only burns the deadline.
            raise JudgeJevError(f"Cloudflare returned malformed JSON: {err}") from err

        # Direct documented model responses are unwrapped. Cloudflare's generic
        # REST envelope may wrap the same object; accept both and validate errors.
        if isinstance(raw, dict) and "success" in raw:
            if raw.get("success") is not True:
                raise JudgeJevError(f"Cloudflare API error: {raw.get('errors') or 'unknown error'}")
            raw = raw.get("result")
        if not isinstance(raw, dict):
            raise JudgeJevError("Cloudflare returned an invalid response object")
        resolved = raw.get("model")
        if resolved != model:
            raise JudgeJevError(
                f"Cloudflare alias {CLOUDFLARE_MODEL!r} resolved to {resolved!r}, "
                f"but rubric pins {model!r}"
            )
        answers = raw.get("answers")
        usage_data = raw.get("usage")
        if not isinstance(answers, dict) or not isinstance(usage_data, dict):
            raise JudgeJevError("Cloudflare response needs answers and usage objects")
        return BackendResponse(
            normalize_answers(answers),
            Usage(usage_data.get("input_tokens"), usage_data.get("output_tokens")),
            request_id,
            model,
            str(resolved),
            "live_model",
        )


def load_recorded(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise JudgeJevError(f"cannot load replay record {path}: {err}") from err
    if not isinstance(data, dict):
        raise JudgeJevError("replay record must be a JSON object")
    return data


def make_backend(
    backend_id: str,
    *,
    pinned: dict[str, dict[str, Any]] | None = None,
    replay_input: Path | None = None,
) -> Backend:
    if backend_id == "typesafe":
        return TypeSafeBackend()
    if backend_id == "cloudflare":
        return CloudflareBackend()
    if backend_id == "replay":
        return ReplayBackend(pinned, load_recorded(replay_input))
    raise JudgeJevError(f"unknown backend {backend_id!r}")

"""Budgeted GEPA integration for instruction-only proposals."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from judge_jev.answers import validate_answers
from judge_jev.backend import make_backend, validate_capabilities
from judge_jev.budget import check_budget
from judge_jev.canonical import CanonicalState
from judge_jev.evaluation import evaluate as evaluate_labeled
from judge_jev.evaluation import index_results, validate_cases
from judge_jev.gates import GateContext, escalate_on_injection, evaluate_gates
from judge_jev.models import JudgeJevError
from judge_jev.paths import rubrics_dir
from judge_jev.routing import evaluate_routing
from judge_jev.rubric import load_rubric, rubric_content_hash
from judge_jev.state_filter import filter_state
from judge_jev.tuning import (
    _proposed_version,
    load_cost_policy,
    read_jsonl,
    validate_support,
)
from judge_jev.typesafe_client import build_questions


@dataclass
class BudgetLedger:
    max_metric_calls: int
    max_task_tokens: int | None = None
    max_tokens_per_call: int | None = None
    metric_calls: int = 0
    reserved_tokens: int = 0
    actual_tokens: int = 0
    usage_complete: bool = True
    provider_calls: int = 0

    def reserve(self) -> None:
        if self.metric_calls + 1 > self.max_metric_calls:
            raise BudgetExhausted("metric-call budget exhausted before the next call")
        if self.max_task_tokens is not None and self.max_tokens_per_call is not None:
            if self.reserved_tokens + self.max_tokens_per_call > self.max_task_tokens:
                raise BudgetExhausted("task-token budget exhausted before the next call")
            self.reserved_tokens += self.max_tokens_per_call
        self.metric_calls += 1

    def settle(self, actual: int | None, *, provider_call: bool = True) -> None:
        if provider_call:
            self.provider_calls += 1
        if actual is None:
            self.usage_complete = False
            if self.max_task_tokens is not None:
                raise BudgetExhausted("provider usage is unavailable for hard task-token accounting")
            return
        if actual < 0:
            raise BudgetExhausted("provider usage must be non-negative")
        if self.max_tokens_per_call is not None and actual > self.max_tokens_per_call:
            raise BudgetExhausted("provider usage exceeded the enforced per-call token cap")
        self.actual_tokens += actual
        if self.max_tokens_per_call is not None:
            self.reserved_tokens -= self.max_tokens_per_call - actual


class BudgetExhausted(JudgeJevError):
    """A hard optimization budget was reached; GEPA must stop immediately."""


class CallLimitedLM:
    """Cap reflector requests before dispatch, including batched completions."""

    def __init__(self, lm: Any, max_calls: int) -> None:
        if max_calls <= 0:
            raise JudgeJevError("reflection-call budget must be positive")
        self.lm = lm
        self.max_calls = max_calls
        self.calls = 0
        self._lock = threading.Lock()

    def _reserve(self, count: int) -> None:
        with self._lock:
            if count < 0 or self.calls + count > self.max_calls:
                raise BudgetExhausted("reflection-call budget exhausted before the next call")
            self.calls += count

    def __call__(self, prompt: Any) -> str:
        self._reserve(1)
        return self.lm(prompt)

    def batch_complete(self, messages_list: list[list[dict[str, Any]]], **kwargs: Any) -> list[str]:
        self._reserve(len(messages_list))
        batch = getattr(self.lm, "batch_complete", None)
        if batch is not None:
            return list(batch(messages_list, **kwargs))
        return [self.lm(messages) for messages in messages_list]

    @property
    def total_cost(self) -> float | None:
        value = getattr(self.lm, "total_cost", None)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @property
    def total_tokens_in(self) -> int | None:
        value = getattr(self.lm, "total_tokens_in", None)
        return value if type(value) is int else None

    @property
    def total_tokens_out(self) -> int | None:
        value = getattr(self.lm, "total_tokens_out", None)
        return value if type(value) is int else None


@contextmanager
def _single_provider_attempts(enabled: bool):
    """Disable provider retries while an enforceable call-count run is active."""

    if not enabled:
        yield
        return
    name = "JUDGE_JEV_MAX_RETRIES"
    previous = os.environ.get(name)
    os.environ[name] = "0"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


class SemanticCache:
    """Opt-in answer cache with hashed keys and atomic, bounded files."""

    def __init__(self, directory: Path, max_bytes: int = 64 * 1024 * 1024) -> None:
        if directory.exists() and directory.is_symlink():
            raise JudgeJevError("cache directory must not be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        self.directory = directory
        self.max_bytes = max_bytes

    @staticmethod
    def key(context: dict[str, Any]) -> str:
        raw = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            return None
        path = self.directory / f"judge-jev-gepa-v1-{key}.json"
        if not path.is_file() or path.is_symlink():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            try:
                path.unlink()
            except OSError:
                pass
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(raw) > self.max_bytes:
            return
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            return
        fd, temporary = tempfile.mkstemp(prefix=f".judge-jev-gepa-v1-{key}-", dir=self.directory)
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                # Windows has no fchmod; the parent directory and atomic replace
                # still provide the platform's available protection.
                pass
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / f"judge-jev-gepa-v1-{key}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        files = sorted(
            (
                path
                for path in self.directory.glob("judge-jev-gepa-v1-*.json")
                if re.fullmatch(r"judge-jev-gepa-v1-[0-9a-f]{64}\.json", path.name)
                and path.is_file()
                and not path.is_symlink()
            ),
            key=lambda path: path.stat().st_mtime_ns,
        )
        total = sum(path.stat().st_size for path in files)
        while files and total > self.max_bytes:
            victim = files.pop(0)
            size = victim.stat().st_size
            victim.unlink()
            total -= size


class JevGEPAAdapter:
    """The exact ``GEPAAdapter`` method contract from GEPA 0.1.4."""

    # GEPA accesses this optional protocol member as an attribute rather than
    # through getattr. None delegates proposal generation to its reflection LM.
    propose_new_texts = None

    def __init__(
        self,
        component: str,
        evaluator: Callable[[dict[str, Any], str], dict[str, Any]],
        cost_policy: dict[str, Any],
        ledger: BudgetLedger,
        batch_factory: Callable[..., Any],
    ) -> None:
        self.component = component
        self.evaluator = evaluator
        self.cost_policy = cost_policy
        self.ledger = ledger
        self.batch_factory = batch_factory

    def evaluate(
        self,
        batch: list[dict[str, Any]],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> Any:
        instruction = candidate[self.component]
        outputs = []
        scores = []
        trajectories = [] if capture_traces else None
        costs = self.cost_policy["costs"]
        default_cost = float(costs.get("default", 5.0))
        for row in batch:
            self.ledger.reserve()
            output = self.evaluator(row, instruction)
            usage = output.get("usage")
            if not isinstance(usage, dict):
                usage = {}
            token_values = [usage.get("input_tokens"), usage.get("output_tokens")]
            if any(value is not None and (type(value) is not int or value < 0) for value in token_values):
                raise JudgeJevError("model result usage must contain non-negative integer or null token counts")
            default_billed = sum(token_values) if all(type(value) is int for value in token_values) else None
            billed_tokens = output.get("newly_billed_tokens", default_billed)
            if billed_tokens is not None and (type(billed_tokens) is not int or billed_tokens < 0):
                raise JudgeJevError("newly_billed_tokens must be a non-negative integer or null")
            provider_call = output.get("newly_billed_model_call", True)
            if type(provider_call) is not bool:
                raise JudgeJevError("newly_billed_model_call must be boolean")
            self.ledger.settle(billed_tokens, provider_call=provider_call)
            predicted = output.get("verdict")
            if predicted not in ("pass", "fail", "review", "escalate", "skip"):
                raise JudgeJevError("model result omitted a valid verdict")
            actual = row["expected_verdict"]
            confusion_cost = float(
                costs.get(
                    f"actual_{actual}_predicted_{predicted}",
                    0.0 if actual == predicted else default_cost,
                )
            )
            review_cost = (
                float(self.cost_policy.get("review_cost", 0.0))
                if predicted in ("review", "escalate")
                else 0.0
            )
            token_rate = float(self.cost_policy.get("token_cost_per_1000", 0.0))
            if token_rate and billed_tokens is None:
                raise JudgeJevError(
                    "model result token usage is unavailable for the declared tuning objective"
                )
            token_cost = token_rate * float(billed_tokens or 0) / 1000.0
            total_cost = confusion_cost + review_cost + token_cost
            score = -total_cost
            feedback = (
                f"Expected {actual}; got {predicted}. "
                f"Objective costs: confusion={confusion_cost}, review={review_cost}, "
                f"tokens={token_cost}, total={total_cost}. "
                f"Deciding answers: {output.get('deciding_answers', [])}. "
                f"Routing reason: {output.get('routing_reason', '')}"
            )
            outputs.append(output)
            scores.append(score)
            if trajectories is not None:
                trajectories.append(
                    {
                        "case_id": row["id"],
                        "expected_verdict": row["expected_verdict"],
                        "output": output,
                        "feedback": feedback,
                    }
                )
        return self.batch_factory(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: Any,
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if self.component not in components_to_update:
            return {}
        records = []
        for trajectory in eval_batch.trajectories or []:
            records.append(
                {
                    "Inputs": {"case_id": trajectory["case_id"]},
                    "Generated Outputs": trajectory["output"],
                    "Feedback": trajectory["feedback"],
                }
            )
        return {self.component: records}


def _question_diff(rubric_id: str, question_id: str, instruction: str) -> str:
    rubric_path = rubrics_dir() / f"{rubric_id}.yaml"
    original = rubric_path.read_text(encoding="utf-8")
    data = yaml.safe_load(original)
    before = data["questions"][question_id]
    original_type = before["type"]
    original_criteria = copy.deepcopy(before.get("criteria"))
    data["version"] = _proposed_version(str(data["version"]))
    data["questions"][question_id]["instructions"] = instruction
    assert data["questions"][question_id]["type"] == original_type
    assert data["questions"][question_id].get("criteria") == original_criteria
    proposed = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=str(rubric_path),
            tofile=str(rubric_path) + ".proposed",
        )
    )


def _live_evaluator(
    rubric_id: str,
    question_id: str,
    backend_id: str,
    cache: SemanticCache | None,
) -> Callable[[dict[str, Any], str], dict[str, Any]]:
    rubric = load_rubric(rubric_id)

    def evaluate(row: dict[str, Any], instruction: str) -> dict[str, Any]:
        candidate = copy.deepcopy(rubric)
        candidate.questions = copy.deepcopy(rubric.questions)
        candidate.questions[question_id]["instructions"] = instruction
        filtered = filter_state(row["input"], candidate.state_filter)
        state = CanonicalState.of(filtered)
        check_budget(state, candidate)
        context = {
            "rubric_id": candidate.id,
            "rubric_version": candidate.version,
            "question_id": question_id,
            "question": candidate.questions[question_id],
            "all_questions": candidate.questions,
            "state_hash": hashlib.sha256(state.text.encode()).hexdigest(),
            "model": candidate.model,
            "backend": backend_id,
        }
        key = SemanticCache.key(context)
        evidence = None
        cache_hit = False
        if cache is not None:
            evidence = cache.get(key)
            cache_hit = evidence is not None
        if evidence is None:
            backend = make_backend(backend_id)
            questions = build_questions(candidate)
            validate_capabilities(backend.capabilities, questions)
            response = backend.system_one(state, questions, candidate.model)
            evidence = {
                "answers": response.answers,
                "usage": response.usage.to_dict(),
                "model": response.resolved_model,
                "requested_model": response.requested_model,
                "request_id": response.request_id,
                "backend_provenance": response.provenance,
            }
            if cache is not None:
                cache.put(key, evidence)
        answers = validate_answers(candidate, evidence.get("answers"))
        routing = evaluate_routing(candidate, answers)
        gates = evaluate_gates(
            GateContext(
                rubric=candidate,
                filtered_state=filtered,
                answers=answers,
                verdict=routing.verdict,
                confidence=routing.confidence,
                confidence_floor=candidate.confidence_floor,
                confidence_candidate=routing.confidence_candidate,
                replay=False,
                budget_ok=True,
            )
        )
        verdict, reason = escalate_on_injection(routing.verdict, routing.reason, gates)
        usage = evidence.get("usage")
        if not isinstance(usage, dict):
            usage = {"input_tokens": None, "output_tokens": None}
        token_values = [usage.get(name) for name in ("input_tokens", "output_tokens")]
        if any(value is not None and (type(value) is not int or value < 0) for value in token_values):
            raise JudgeJevError("model result returned invalid token usage")
        billed_tokens = sum(token_values) if all(type(value) is int for value in token_values) else None
        output = {
            "rubric_id": candidate.id,
            "rubric_version": candidate.version,
            "verdict": verdict,
            "routing_reason": reason,
            "stage": routing.stage,
            "deciding_answers": list(routing.deciding_answers),
            "confidence": routing.confidence,
            "confidence_floor": candidate.confidence_floor,
            "answers": answers,
            "usage": usage,
            "newly_billed_tokens": 0 if cache_hit else billed_tokens,
            "newly_billed_model_call": not cache_hit,
            "backend": backend_id,
            "model": evidence.get("model"),
            "requested_model": evidence.get("requested_model"),
            "request_id": evidence.get("request_id"),
            "backend_provenance": evidence.get("backend_provenance"),
            "rubric_hash": rubric_content_hash(candidate),
            "deterministic_gates": [gate.to_dict() for gate in gates],
            "routing_trace": routing.trace_dict(),
        }
        return output

    # Neither verified Jev provider currently exposes a request-level hard output
    # token bound. Strict hard-spend mode refuses before a request; explicit
    # call-count mode instead uses hard request counters and reports usage.
    evaluate.hard_token_limit = None  # type: ignore[attr-defined]
    return evaluate


def _batch_report(rows: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    result_rows = []
    for row, output in zip(rows, outputs):
        result = copy.deepcopy(output)
        result.setdefault("rubric_id", row["rubric_id"])
        result_rows.append(
            {"case_id": row["id"], "provenance": "live_gepa_gate", "result": result}
        )
    return evaluate_labeled(rows, index_results(result_rows))


def _cell_cost(policy: dict[str, Any], actual: str, predicted: str) -> float:
    costs = policy["costs"]
    return float(
        costs.get(
            f"actual_{actual}_predicted_{predicted}",
            0.0 if actual == predicted else costs.get("default", 0.0),
        )
    )


def instruction_proposal(
    rubric_id: str,
    question_id: str,
    cases_path: Path,
    *,
    live: bool = False,
    budget_mode: str | None = None,
    metric_budget: int = 0,
    reflection_call_budget: int = 0,
    task_token_cap: int = 0,
    max_tokens_per_call: int = 0,
    reflection_cost_cap: float = 0.0,
    reflection_model: Any = None,
    backend_id: str = "typesafe",
    cache_dir: Path | None = None,
    seed: int = 1,
    minimum_support: int = 2,
    max_gate_candidates: int = 2,
    optimize_fn: Callable[..., Any] | None = None,
    batch_factory: Callable[..., Any] | None = None,
    evaluator: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rubric = load_rubric(rubric_id)
    if question_id not in rubric.questions:
        raise JudgeJevError(f"unknown question {question_id!r}")
    question = rubric.questions[question_id]
    try:
        rows = validate_cases(read_jsonl(cases_path))
    except (ValueError, OSError) as err:
        raise JudgeJevError(str(err)) from err
    mismatched = [row["id"] for row in rows if row["rubric_id"] != rubric_id]
    if mismatched:
        raise JudgeJevError(
            f"instruction corpus contains cases for another rubric: {sorted(mismatched)}"
        )
    policy = load_cost_policy()
    validate_support(rows, policy.get("required_classes", []), minimum_support)
    train = [row for row in rows if row["split"] == "train"]
    heldout = [row for row in rows if row["split"] == "heldout"]
    redteam = [row for row in rows if row["split"] == "frozen-redteam"]
    plan = {
        "version": 1,
        "engine": "GEPA 0.1.4",
        "proposal_only": True,
        "live": live,
        "rubric_id": rubric_id,
        "question_id": question_id,
        "budget_mode": budget_mode,
        "preserved": {
            "question_type": question["type"],
            "criteria": question.get("criteria"),
            "numeric_policy": True,
        },
        "split_support": {"train": len(train), "heldout": len(heldout), "frozen-redteam": len(redteam)},
        "api_calls": 0,
    }
    if not live:
        plan["status"] = "dry-run; no GEPA, model, or reflector calls were made"
        return plan
    if budget_mode not in ("calls", "hard-spend"):
        raise JudgeJevError("live GEPA requires --budget-mode calls or --budget-mode hard-spend")
    if metric_budget <= 0 or reflection_call_budget <= 0:
        raise JudgeJevError("live GEPA requires positive metric and reflection-call budgets")
    if not 1 <= max_gate_candidates <= 8:
        raise JudgeJevError("max gate candidates must be from 1 to 8")
    if not reflection_model:
        raise JudgeJevError("live GEPA requires --reflection-model")
    if budget_mode == "hard-spend":
        if min(task_token_cap, max_tokens_per_call) <= 0 or reflection_cost_cap <= 0:
            raise JudgeJevError(
                "hard-spend mode requires positive task-token, per-call-token, and reflection-cost caps"
            )
        raise JudgeJevError(
            "hard-spend mode is unavailable: verified Jev providers cannot enforce a request-level "
            "output-token cap and GEPA observes reflector cost only after a request; no call was made"
        )
    if task_token_cap or max_tokens_per_call or reflection_cost_cap:
        raise JudgeJevError(
            "call-count mode does not treat token or dollar values as hard caps; remove the hard-spend "
            "flags or choose --budget-mode hard-spend"
        )
    # Reserve a baseline plus a bounded candidate subset across every split.
    # This yields comparable train, held-out, and frozen-red-team reports without
    # allowing GEPA's adaptive search to consume their calls.
    reserved_final = (1 + max_gate_candidates) * len(rows)
    optimization_budget = metric_budget - reserved_final
    if optimization_budget < len(train):
        raise JudgeJevError("metric budget is too small after reserving independent held-out/red-team gates")
    cache = SemanticCache(cache_dir) if cache_dir is not None else None
    evaluator = evaluator or _live_evaluator(rubric_id, question_id, backend_id, cache)
    if optimize_fn is None or batch_factory is None:
        try:
            from gepa import optimize as gepa_optimize
            from gepa.core.adapter import EvaluationBatch
        except ImportError as err:
            raise JudgeJevError(
                "GEPA support is optional; install with `uv sync --extra tuning` (pinned GEPA 0.1.4)"
            ) from err
        optimize_fn = gepa_optimize
        batch_factory = EvaluationBatch
    if callable(reflection_model):
        base_reflection_lm = reflection_model
    else:
        try:
            from gepa.lm import LM
        except ImportError as err:
            raise JudgeJevError(
                "GEPA support is optional; install with `uv sync --extra tuning` (pinned GEPA 0.1.4)"
            ) from err
        # Retries would turn one authorized reflection request into multiple paid
        # requests. Call-count mode therefore disables them explicitly.
        base_reflection_lm = LM(str(reflection_model), num_retries=0)
    reflection_lm = CallLimitedLM(base_reflection_lm, reflection_call_budget)
    # The adapter, not GEPA's post-batch stopper, owns the hard pre-call bound.
    # GEPA can schedule a batch that crosses its own max_metric_calls value, so
    # independent gate calls remain unavailable to optimization until it returns.
    ledger = BudgetLedger(optimization_budget)
    adapter = JevGEPAAdapter(question_id, evaluator, policy, ledger, batch_factory)
    with _single_provider_attempts(True):
        result = optimize_fn(
            seed_candidate={question_id: str(question.get("instructions") or "")},
            trainset=train,
            valset=train,
            adapter=adapter,
            reflection_lm=reflection_lm,
            candidate_selection_strategy="pareto",
            max_metric_calls=optimization_budget,
            # GEPA checks stoppers between iterations, while one new iteration can
            # schedule a whole train batch. Stop before that batch cannot fit.
            stop_callbacks=lambda _state: ledger.metric_calls + len(train) > optimization_budget,
            seed=seed,
            cache_evaluation=True,
            display_progress_bar=False,
        )
        best = result.best_candidate
        if not isinstance(best, dict) or question_id not in best:
            raise JudgeJevError("GEPA returned no instruction candidate for the requested question")
        # Independent reports are evaluated only after GEPA has stopped.
        ledger.max_metric_calls = metric_budget
        seed_candidate = {question_id: str(question.get("instructions") or "")}
        raw_candidates = list(getattr(result, "candidates", []) or [best])
        scores = list(getattr(result, "val_aggregate_scores", []) or [])
        front_by_case = getattr(result, "per_val_instance_best_candidates", {}) or {}
        frontier_indices = {
            index
            for indices in front_by_case.values()
            for index in indices
            if type(index) is int and 0 <= index < len(raw_candidates)
        }
        try:
            best_index = int(result.best_idx)
        except (AttributeError, TypeError, ValueError):
            best_index = next((i for i, candidate in enumerate(raw_candidates) if candidate == best), 0)
        ordered_indices = [best_index] + sorted(
            frontier_indices - {best_index},
            key=lambda index: scores[index] if index < len(scores) else float("-inf"),
            reverse=True,
        )
        ordered_indices.extend(index for index in range(len(raw_candidates)) if index not in ordered_indices)
        selected: list[tuple[int, dict[str, str]]] = []
        seen_instructions = {seed_candidate[question_id]}
        for index in ordered_indices:
            candidate = raw_candidates[index]
            if not isinstance(candidate, dict) or not isinstance(candidate.get(question_id), str):
                continue
            instruction = candidate[question_id]
            if instruction in seen_instructions:
                continue
            seen_instructions.add(instruction)
            selected.append((index, candidate))
            if len(selected) == max_gate_candidates:
                break
        baseline_gate = adapter.evaluate(rows, seed_candidate, capture_traces=False)
        candidate_gates = [
            (index, candidate, adapter.evaluate(rows, candidate, capture_traces=False))
            for index, candidate in selected
        ]
    gate_rows = rows
    baseline_outputs = list(baseline_gate.outputs)
    baseline_report = _batch_report(gate_rows, baseline_outputs)
    baseline_complete = baseline_report["counts"]["evaluated"] == baseline_report["counts"]["labeled"]
    candidate_reports = []
    for index, candidate, gate in candidate_gates:
        outputs = list(gate.outputs)
        report = _batch_report(gate_rows, outputs)
        complete = (
            baseline_complete
            and report["counts"]["evaluated"] == report["counts"]["labeled"]
        )
        heldout_regressions = []
        for row, before_output, after_output in zip(rows, baseline_outputs, outputs):
            if row["split"] != "heldout":
                continue
            before = before_output.get("verdict")
            after = after_output.get("verdict")
            if _cell_cost(policy, row["expected_verdict"], after) > _cell_cost(
                policy, row["expected_verdict"], before
            ):
                heldout_regressions.append(row["id"])
        redteam_gate = report["redteam_regression"]
        redteam_regressions = [item["case_id"] for item in redteam_gate["regressions"]]
        accepted = complete and not heldout_regressions and redteam_gate["hard_gate_passed"]
        candidate_reports.append(
            {
                "candidate_index": index,
                "instruction": candidate[question_id],
                "gepa_train_score": scores[index] if index < len(scores) else None,
                "gate_evidence_complete": complete,
                "heldout_hard_gate_passed": not heldout_regressions,
                "heldout_regressions": heldout_regressions,
                "redteam_hard_gate_passed": redteam_gate["hard_gate_passed"],
                "redteam_regressions": redteam_regressions,
                "metrics": report,
                "diff": _question_diff(rubric_id, question_id, candidate[question_id]),
                "status": "proposal generated for review" if accepted else "proposal rejected by independent gates",
            }
        )
    internal_frontier = sorted(frontier_indices | {best_index})
    best_report = next(
        (item for item in candidate_reports if item["candidate_index"] == best_index),
        candidate_reports[0] if candidate_reports else None,
    )
    plan.update(
        {
            "api_calls": ledger.metric_calls,
            "provider_calls": ledger.provider_calls,
            "metric_call_limit": metric_budget,
            "reflection_calls": reflection_lm.calls,
            "reflection_call_limit": reflection_call_budget,
            "provider_retries": 0,
            "reflector_retries": 0,
            "task_tokens": ledger.actual_tokens if ledger.usage_complete else None,
            "task_token_measurement": "complete" if ledger.usage_complete else "unavailable",
            "reflection_tokens": (
                None
                if reflection_lm.total_tokens_in is None or reflection_lm.total_tokens_out is None
                else reflection_lm.total_tokens_in + reflection_lm.total_tokens_out
            ),
            "reflection_cost_usd": reflection_lm.total_cost,
            "hard_token_or_dollar_cap": False,
            "gepa_total_metric_calls": getattr(result, "total_metric_calls", None),
            "best_score": getattr(result, "best_score", None),
            "candidate_count": len(getattr(result, "candidates", [])),
            "baseline_gate_metrics": baseline_report,
            "gepa_selected_candidate_index": best_index,
            "gepa_internal_frontier": [
                {
                    "candidate_index": index,
                    "instruction": raw_candidates[index].get(question_id),
                    "train_score": scores[index] if index < len(scores) else None,
                }
                for index in internal_frontier
            ],
            "candidate_proposals": candidate_reports,
            "selection_note": (
                "GEPA's selected index reflects adaptive train performance only; "
                "review every independently reported candidate and its split metrics"
            ),
            "gate_evidence_complete": bool(best_report and best_report["gate_evidence_complete"]),
            "heldout_hard_gate_passed": bool(best_report and best_report["heldout_hard_gate_passed"]),
            "heldout_regressions": best_report["heldout_regressions"] if best_report else [],
            "redteam_hard_gate_passed": bool(best_report and best_report["redteam_hard_gate_passed"]),
            "redteam_regressions": best_report["redteam_regressions"] if best_report else [],
            "proposal_gate_metrics": best_report["metrics"] if best_report else None,
            "diff": best_report["diff"] if best_report else None,
            "status": best_report["status"] if best_report else "GEPA produced no new candidate",
        }
    )
    return plan

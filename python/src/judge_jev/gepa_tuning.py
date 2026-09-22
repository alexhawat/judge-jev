"""Budgeted GEPA integration for instruction-only proposals."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from judge_jev.backend import make_backend, validate_capabilities
from judge_jev.canonical import CanonicalState
from judge_jev.models import JudgeJevError
from judge_jev.routing import route_verdict
from judge_jev.rubric import load_rubric
from judge_jev.state_filter import filter_state
from judge_jev.tuning import (
    _proposed_version,
    join_corpus,
    load_cost_policy,
    read_jsonl,
    validate_support,
)
from judge_jev.typesafe_client import build_questions


@dataclass
class BudgetLedger:
    max_metric_calls: int
    max_task_tokens: int
    max_tokens_per_call: int
    metric_calls: int = 0
    reserved_tokens: int = 0
    actual_tokens: int = 0

    def reserve(self) -> None:
        if self.metric_calls + 1 > self.max_metric_calls:
            raise JudgeJevError("metric-call budget exhausted before the next call")
        if self.reserved_tokens + self.max_tokens_per_call > self.max_task_tokens:
            raise JudgeJevError("task-token budget exhausted before the next call")
        self.metric_calls += 1
        self.reserved_tokens += self.max_tokens_per_call

    def settle(self, actual: int) -> None:
        if actual < 0 or actual > self.max_tokens_per_call:
            raise JudgeJevError("provider usage exceeded the reserved per-call token cap")
        self.actual_tokens += actual
        self.reserved_tokens -= self.max_tokens_per_call - actual


class SemanticCache:
    """Opt-in answer cache with hashed keys and atomic, bounded files."""

    def __init__(self, directory: Path, max_bytes: int = 64 * 1024 * 1024) -> None:
        if directory.exists() and directory.is_symlink():
            raise JudgeJevError("cache directory must not be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory = directory
        self.max_bytes = max_bytes

    @staticmethod
    def key(context: dict[str, Any]) -> str:
        raw = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(raw).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
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
        fd, temporary = tempfile.mkstemp(prefix=f".{key}-", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.directory / f"{key}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        files = sorted(
            (path for path in self.directory.glob("*.json") if path.is_file() and not path.is_symlink()),
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
            try:
                self.ledger.reserve()
                output = self.evaluator(row, instruction)
                usage = output.get("usage") or {}
                tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
                self.ledger.settle(tokens)
                predicted = output["verdict"]
                actual = row["expected_verdict"]
                cost = float(costs.get(f"actual_{actual}_predicted_{predicted}", 0.0 if actual == predicted else default_cost))
                score = 1.0 / (1.0 + cost)
                feedback = (
                    f"Expected {actual}; got {predicted}. "
                    f"Deciding answers: {output.get('deciding_answers', [])}. "
                    f"Routing reason: {output.get('routing_reason', '')}"
                )
            except Exception as err:  # GEPA contract: individual failures score zero.
                output = {"error": f"{type(err).__name__}: {err}"}
                score = 0.0
                feedback = f"Evaluation failed: {output['error']}"
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
    rubric_path = Path(__file__).resolve().parents[3] / "shared" / "rubrics" / f"{rubric_id}.yaml"
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
        if cache is not None:
            cached = cache.get(key)
            if cached is not None:
                return cached
        backend = make_backend(backend_id)
        questions = build_questions(candidate)
        validate_capabilities(backend.capabilities, questions)
        response = backend.system_one(state, questions, candidate.model)
        verdict, reason, _stage, deciding, confidence = route_verdict(candidate, response.answers)
        output = {
            "verdict": verdict,
            "routing_reason": reason,
            "deciding_answers": deciding,
            "confidence": confidence,
            "answers": response.answers,
            "usage": response.usage.to_dict(),
            "backend": backend_id,
            "model": response.resolved_model,
        }
        if cache is not None:
            cache.put(key, output)
        return output

    return evaluate


def instruction_proposal(
    rubric_id: str,
    question_id: str,
    cases_path: Path,
    *,
    live: bool = False,
    metric_budget: int = 0,
    task_token_cap: int = 0,
    max_tokens_per_call: int = 0,
    reflection_cost_cap: float = 0.0,
    reflection_model: str | None = None,
    backend_id: str = "typesafe",
    cache_dir: Path | None = None,
    seed: int = 1,
    minimum_support: int = 2,
    optimize_fn: Callable[..., Any] | None = None,
    batch_factory: Callable[..., Any] | None = None,
    evaluator: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rubric = load_rubric(rubric_id)
    if question_id not in rubric.questions:
        raise JudgeJevError(f"unknown question {question_id!r}")
    question = rubric.questions[question_id]
    rows = read_jsonl(cases_path)
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
    if min(metric_budget, task_token_cap, max_tokens_per_call) <= 0 or reflection_cost_cap <= 0:
        raise JudgeJevError("live GEPA requires positive metric, task-token, per-call-token, and reflection-cost caps")
    if not reflection_model:
        raise JudgeJevError("live GEPA requires --reflection-model")
    reserved_final = 2 * (len(heldout) + len(redteam))
    optimization_budget = metric_budget - reserved_final
    if optimization_budget <= 0:
        raise JudgeJevError("metric budget is too small after reserving independent held-out/red-team gates")
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
    ledger = BudgetLedger(metric_budget, task_token_cap, max_tokens_per_call)
    cache = SemanticCache(cache_dir) if cache_dir is not None else None
    evaluator = evaluator or _live_evaluator(rubric_id, question_id, backend_id, cache)
    adapter = JevGEPAAdapter(question_id, evaluator, policy, ledger, batch_factory)
    result = optimize_fn(
        seed_candidate={question_id: str(question.get("instructions") or "")},
        trainset=train,
        valset=train,
        adapter=adapter,
        reflection_lm=reflection_model,
        candidate_selection_strategy="pareto",
        max_metric_calls=optimization_budget,
        max_reflection_cost=reflection_cost_cap,
        seed=seed,
        cache_evaluation=True,
        display_progress_bar=False,
    )
    best = result.best_candidate
    if not isinstance(best, dict) or question_id not in best:
        raise JudgeJevError("GEPA returned no instruction candidate for the requested question")
    # Independent gates are evaluated only after GEPA has stopped. Both seed and
    # proposal consume the budget reserved before optimization.
    seed_candidate = {question_id: str(question.get("instructions") or "")}
    baseline_gate = adapter.evaluate(heldout + redteam, seed_candidate, capture_traces=False)
    proposal_gate = adapter.evaluate(heldout + redteam, best, capture_traces=False)
    redteam_start = len(heldout)
    regressions = []
    for offset, row in enumerate(redteam):
        before = baseline_gate.outputs[redteam_start + offset].get("verdict")
        after = proposal_gate.outputs[redteam_start + offset].get("verdict")
        if before != "pass" and after == "pass":
            regressions.append(row["id"])
    plan.update(
        {
            "api_calls": ledger.metric_calls,
            "task_tokens": ledger.actual_tokens,
            "gepa_total_metric_calls": getattr(result, "total_metric_calls", None),
            "best_score": getattr(result, "best_score", None),
            "candidate_count": len(getattr(result, "candidates", [])),
            "redteam_hard_gate_passed": not regressions,
            "redteam_regressions": regressions,
            "heldout_scores": proposal_gate.scores[: len(heldout)],
            "frozen_redteam_scores": proposal_gate.scores[len(heldout) :],
            "diff": _question_diff(rubric_id, question_id, best[question_id]),
            "status": "proposal rejected by frozen red-team" if regressions else "proposal generated for review",
        }
    )
    return plan

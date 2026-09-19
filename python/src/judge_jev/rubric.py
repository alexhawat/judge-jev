"""Rubric loading and validation from shared YAML."""

from __future__ import annotations

from typing import Any

import yaml

from judge_jev.models import (
    STAGE_ORDER,
    VERDICTS,
    Condition,
    Rubric,
    RoutingRule,
    RubricError,
)
from judge_jev.paths import rubrics_dir
from judge_jev.routing import FIELDS_BY_TYPE, OPS, TEXT_FIELDS

QUESTION_TYPES = frozenset(FIELDS_BY_TYPE)


def list_rubric_ids() -> list[str]:
    root = rubrics_dir()
    return sorted(p.stem for p in root.glob("*.yaml"))


def _parse_condition(raw: Any, where: str) -> Condition:
    if not isinstance(raw, dict):
        raise RubricError(f"{where}: condition must be a mapping, got {type(raw).__name__}")
    missing = {"answer", "field", "op", "value"} - set(raw)
    if missing:
        raise RubricError(f"{where}: condition is missing {sorted(missing)}")
    return Condition(
        answer=str(raw["answer"]),
        field=str(raw["field"]),
        op=str(raw["op"]),
        value=raw["value"],
    )


def _parse_rules(raw_rules: Any, questions: dict[str, Any]) -> tuple[RoutingRule, ...]:
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RubricError("routing.rules must be a non-empty list")

    rules: list[RoutingRule] = []
    for index, raw in enumerate(raw_rules):
        where = f"routing.rules[{index}]"
        if not isinstance(raw, dict):
            raise RubricError(f"{where}: rule must be a mapping")

        verdict = raw.get("verdict")
        if verdict not in VERDICTS:
            raise RubricError(f"{where}: verdict must be one of {list(VERDICTS)}, got {verdict!r}")

        is_default = bool(raw.get("default", False))
        raw_conditions = raw.get("all", [])
        if is_default and raw_conditions:
            raise RubricError(f"{where}: a default rule cannot also declare conditions")
        if not is_default and not raw_conditions:
            raise RubricError(f"{where}: rule needs either 'all' conditions or 'default: true'")

        conditions = tuple(
            _parse_condition(c, f"{where}.all[{i}]") for i, c in enumerate(raw_conditions)
        )
        for i, condition in enumerate(conditions):
            _validate_condition(condition, questions, f"{where}.all[{i}]")

        rules.append(
            RoutingRule(
                verdict=verdict,
                reason=str(raw.get("reason", "")),
                conditions=conditions,
                default=is_default,
            )
        )

    defaults = [i for i, r in enumerate(rules) if r.default]
    if len(defaults) > 1:
        raise RubricError(f"routing.rules declares {len(defaults)} default rules; at most one is allowed")
    if defaults and defaults[0] != len(rules) - 1:
        raise RubricError("the default rule must be last; rules after it can never match")
    return tuple(rules)


def _validate_condition(condition: Condition, questions: dict[str, Any], where: str) -> None:
    question = questions.get(condition.answer)
    if question is None:
        raise RubricError(
            f"{where}: references answer '{condition.answer}', which is not a question in this rubric"
        )
    if condition.op not in OPS:
        raise RubricError(f"{where}: unknown op {condition.op!r}; expected one of {sorted(OPS)}")

    qtype = question.get("type")
    allowed = FIELDS_BY_TYPE.get(qtype, frozenset())
    if condition.field not in allowed:
        raise RubricError(
            f"{where}: field {condition.field!r} is not available on a {qtype!r} answer; "
            f"expected one of {sorted(allowed)}"
        )

    if condition.field in TEXT_FIELDS:
        if condition.op not in ("==", "!="):
            raise RubricError(f"{where}: field {condition.field!r} supports only == and !=")
        # A choice can only ever be one of its declared labels.
        labels = question.get("criteria") or {}
        if isinstance(labels, dict) and str(condition.value) not in labels:
            raise RubricError(
                f"{where}: value {condition.value!r} is not a label of "
                f"'{condition.answer}'; expected one of {sorted(labels)}"
            )
    else:
        try:
            float(condition.value)
        except (TypeError, ValueError):
            raise RubricError(
                f"{where}: field {condition.field!r} needs a numeric value, got {condition.value!r}"
            ) from None


def _validate_questions(questions: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(questions, dict) or not questions:
        raise RubricError("questions must be a non-empty mapping")
    for name, spec in questions.items():
        if not isinstance(spec, dict):
            raise RubricError(f"questions.{name}: must be a mapping")
        qtype = spec.get("type")
        if qtype not in QUESTION_TYPES:
            raise RubricError(
                f"questions.{name}: type must be one of {sorted(QUESTION_TYPES)}, got {qtype!r}"
            )
        stage = spec.get("stage")
        if stage not in STAGE_ORDER:
            raise RubricError(
                f"questions.{name}: stage must be one of {list(STAGE_ORDER)}, got {stage!r}"
            )
        criteria = spec.get("criteria")
        if qtype == "choice" and not isinstance(criteria, dict):
            raise RubricError(f"questions.{name}: a choice question needs a criteria mapping")
        if qtype == "score" and not (isinstance(criteria, list) and criteria):
            raise RubricError(f"questions.{name}: a score question needs a non-empty criteria list")
    return questions


def load_rubric(rubric_id: str) -> Rubric:
    path = rubrics_dir() / f"{rubric_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Rubric not found: {rubric_id}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RubricError(f"{rubric_id}: rubric must be a YAML mapping")

    for required in ("id", "version", "model", "stakes"):
        if required not in data:
            raise RubricError(f"{rubric_id}: missing required key {required!r}")

    questions = _validate_questions(data.get("questions"))
    floors = data.get("confidence_floors", {}) or {}
    stakes = str(data["stakes"])
    if stakes not in floors:
        raise RubricError(
            f"{rubric_id}: stakes {stakes!r} has no entry in confidence_floors "
            f"({sorted(floors)}); automatic verdicts would be ungated"
        )

    routing = data.get("routing") or {}
    rules = _parse_rules(routing.get("rules"), questions)

    return Rubric(
        id=str(data["id"]),
        version=str(data["version"]),
        description=str(data.get("description", "")),
        model=str(data["model"]),
        stakes=stakes,
        confidence_floors={k: float(v) for k, v in floors.items()},
        state_filter=list(data.get("state_filter", []) or []),
        questions=questions,
        rules=rules,
    )


def show_rubric(rubric_id: str) -> str:
    rubric = load_rubric(rubric_id)
    lines = [
        f"id: {rubric.id}",
        f"version: {rubric.version}",
        f"model: {rubric.model}",
        f"stakes: {rubric.stakes} (confidence floor {rubric.confidence_floor:.2f})",
        f"questions: {len(rubric.questions)}",
    ]
    # Funnel order, so the listing reads the way the funnel runs and both runtimes
    # print the same thing regardless of mapping order.
    def in_funnel_order(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        name, spec = item
        return (STAGE_ORDER.index(spec["stage"]), name)

    for name, q in sorted(rubric.questions.items(), key=in_funnel_order):
        lines.append(f"  - {name} ({q['type']}, stage={q['stage']})")
    lines.append(f"rules: {len(rubric.rules)}")
    for rule in rubric.rules:
        if rule.default:
            lines.append(f"  - {rule.verdict} (default)")
        else:
            clauses = " and ".join(
                f"{c.answer}.{c.field} {c.op} {c.value}" for c in rule.conditions
            )
            lines.append(f"  - {rule.verdict} when {clauses}")
    return "\n".join(lines)

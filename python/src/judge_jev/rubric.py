"""Rubric loading from shared YAML."""

from __future__ import annotations

from pathlib import Path

import yaml

from judge_jev.models import Rubric
from judge_jev.paths import rubrics_dir


def list_rubric_ids() -> list[str]:
    root = rubrics_dir()
    return sorted(p.stem for p in root.glob("*.yaml"))


def load_rubric(rubric_id: str) -> Rubric:
    path = rubrics_dir() / f"{rubric_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Rubric not found: {rubric_id}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Rubric(
        id=data["id"],
        version=data["version"],
        description=data.get("description", ""),
        model=data["model"],
        stakes=data["stakes"],
        confidence_floors=data.get("confidence_floors", {}),
        state_filter=data.get("state_filter", []),
        questions=data["questions"],
        routing=data.get("routing", {}),
    )


def show_rubric(rubric_id: str) -> str:
    rubric = load_rubric(rubric_id)
    lines = [
        f"id: {rubric.id}",
        f"version: {rubric.version}",
        f"model: {rubric.model}",
        f"stakes: {rubric.stakes}",
        f"questions: {len(rubric.questions)}",
    ]
    for name, q in rubric.questions.items():
        lines.append(f"  - {name} ({q['type']}, stage={q['stage']})")
    return "\n".join(lines)

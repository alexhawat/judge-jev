"""Selecting the state a rubric actually judges.

`state_filter` used to be whole top-level keys and nothing else:

    return {k: source[k] for k in keys if k in source}

A key was all-or-nothing. There was no way to send `ticket.subject` without
`ticket.body`, or to keep `steps[].tool` while dropping `steps[].output`.
Everything sent is billed and counts against the token budget, so the only lever a
caller had was restructuring the input before calling — which means every caller
writes its own pre-processor, and the rubric's declared `state_filter` stops
describing what is actually judged. The instructions already talk about nested
paths ("`steps` performs writes or destructive actions not justified by `goal`")
while the filter could not select one.

The syntax matches the notation the instructions already use:

    state_filter:
      - goal
      - steps[].tool
      - steps[].input
      - final_output

  * `a` selects a top-level key, exactly as before.
  * `a.b` selects a nested key.
  * `a[]` maps over a list; `a[].b` projects a field from each element.
  * The filtered state keeps the original shape, so a path written in
    `instructions` still resolves against what the model is sent: `steps[].tool`
    yields `{"steps": [{"tool": …}, …]}`, not a bare list of tool names.

Nesting a list inside a list (`a[][]`) is not supported; a path segment is a key,
optionally followed by `[]`.

A path that does not resolve is dropped with a warning and the run continues,
which is the long-standing behaviour and the right default: `fixtures/assistant-
reply-skip.json` deliberately omits `reply` so the screen stage can short-circuit
to `skip`. But a warning is too quiet when the absence is a bug rather than a
case, because a question then judges data that was never there. So a path can opt
in to being required:

    state_filter:
      - { path: ticket.subject, required: true }

and a missing one fails the run instead. The mapping form was chosen over a
sigil (`ticket.subject!`) because it reads as data rather than punctuation, it
matches the inline-mapping style the routing rules already use, and it leaves room
for a future per-path option without inventing more syntax.

In YAML, `[` and `]` are flow indicators, so a path is written bare in a block
sequence (`- steps[].tool`) and in block mapping form, but must be quoted inside an
inline mapping: `{ path: "steps[].tool", required: true }`. The error from an
unquoted one comes from the YAML parser and does not mention state_filter, so both
runtimes pin it in a test.

"Missing" means the walk cannot proceed: a key is absent, or a segment is applied
to the wrong kind of value (a key into a list, `[]` into an object). An element
that lacks a projected field is not missing — the list is there, so it keeps its
length and that element contributes `{}`. Preserving the length matters: dropping
elements would silently rewrite a trajectory that questions like `locate.looping`
are counting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from judge_jev.models import JudgeJevError
from judge_jev.typesafe_client import MOCK_ANSWERS_KEY

# A `[]` segment: map over the list at this point.
EACH = object()
MISSING = object()


@dataclass(frozen=True)
class StatePath:
    """One `state_filter` entry: a path, and whether its absence is fatal."""

    path: str
    required: bool = False


class StatePathError(ValueError):
    """A `state_filter` path is malformed. Raised at rubric load, never mid-run."""


def parse_path(path: str) -> tuple[Any, ...]:
    """Split a path into segments: key names, and EACH for every `[]`.

    Both runtimes parse identically; `rust/src/state_filter.rs` is the mirror.
    """
    if not path:
        raise StatePathError("a state_filter path cannot be empty")
    segments: list[Any] = []
    for raw in path.split("."):
        name = raw
        each = name.endswith("[]")
        if each:
            name = name[:-2]
        if not name:
            raise StatePathError(f"state_filter path {path!r} has an empty segment")
        if "[" in name or "]" in name:
            raise StatePathError(
                f"state_filter path {path!r}: a segment is a key, optionally followed by '[]'"
            )
        segments.append(name)
        if each:
            segments.append(EACH)
    return tuple(segments)


def _project(value: Any, segments: tuple[Any, ...]) -> Any:
    """The part of *value* that *segments* selects, keeping the original shape.

    MISSING means the path does not resolve here. JSON null is a real selected
    value and must remain distinguishable from absence.
    """
    if not segments:
        return value
    head, rest = segments[0], segments[1:]

    if head is EACH:
        if not isinstance(value, list):
            return MISSING
        if not rest:
            return list(value)
        # An element that does not carry the projected field contributes an empty
        # object rather than vanishing, so the list keeps its length.
        projected = []
        for element in value:
            child = _project(element, rest)
            projected.append({} if child is MISSING else child)
        return projected

    if not isinstance(value, dict) or head not in value:
        return MISSING
    child = _project(value[head], rest)
    if child is MISSING:
        return MISSING
    return {head: child}


def _merge(existing: Any, addition: Any) -> Any:
    """Fold one path's projection into the result built so far."""
    if isinstance(existing, dict) and isinstance(addition, dict):
        for key, value in addition.items():
            existing[key] = _merge(existing[key], value) if key in existing else value
        return existing
    if isinstance(existing, list) and isinstance(addition, list):
        for index, value in enumerate(addition):
            if index < len(existing):
                existing[index] = _merge(existing[index], value)
            else:
                existing.append(value)
        return existing
    # Nothing to fold: a broader selection of the same key already covers this one.
    return existing


def filter_state(raw: dict[str, Any], paths: list[StatePath]) -> dict[str, Any]:
    """Keep only what a rubric declares, dropping the mock override block."""
    source = {k: v for k, v in raw.items() if k != MOCK_ANSWERS_KEY}
    if not paths:
        return source

    filtered: dict[str, Any] = {}
    missing: list[str] = []
    missing_required: list[str] = []
    resolved: list[tuple[tuple[Any, ...], Any]] = []
    for entry in paths:
        if entry.path == MOCK_ANSWERS_KEY:
            continue
        segments = parse_path(entry.path)
        projected = _project(source, segments)
        if projected is MISSING:
            (missing_required if entry.required else missing).append(entry.path)
            continue
        resolved.append((segments, projected))

    if missing_required:
        # An operational failure, not a malformed rubric: the rubric is fine and the
        # input is not. Loud, because a question reading this path would otherwise
        # judge data that was never there.
        raise JudgeJevError(
            "required state_filter paths are missing from the input: "
            + ", ".join(missing_required)
        )
    if missing:
        # Questions referencing these paths will be judging absent data.
        logger.warning("state_filter paths missing from input: {}", ", ".join(missing))

    # Merge broad selections first. A selected parent owns its complete value, so
    # a child projection can never hollow it out merely because paths were listed
    # in a different order. Required descendants were still checked above.
    selected: list[tuple[Any, ...]] = []
    for segments, projected in sorted(resolved, key=lambda item: len(item[0])):
        if any(segments[: len(parent)] == parent for parent in selected):
            continue
        _merge(filtered, projected)
        selected.append(segments)
    return filtered

#!/usr/bin/env bash
# Adapter docs must not teach `--mock` as the normal way to run judge-jev.
#
# Mock answers are canned; a mocked judgment is a wiring smoke test, never a safety
# check (see AGENTS.md and examples/README.md). That did not stop five of seven
# adapters/*/README.md files from using `--mock` in their primary example, which
# quietly taught the opposite (issue #5). This is the guard that stops it drifting
# back, the same way examples/run-all.sh guards the examples and the
# `mergecraft-pin` job in .github/workflows/ci.yml guards that pin.
#
# `--mock` is still allowed:
#   - bracketed as an optional flag in a command-contract line, e.g. `[--mock]`
#   - inside a fenced example that a "smoke-test" (or "smoke test") sentence
#     introduces, in the paragraph immediately before that fence, or in a comment
#     on the flagged line itself
# The justification has to come BEFORE the fence, not after: prose that follows a
# fence and mentions --mock in passing ("you can also add --mock to smoke-test
# this") describes a further, separate invocation -- it does not retroactively
# excuse a --mock that has already crept into the example above it. That is the
# exact shape the drift takes, so trailing prose does not count.
# A bare `--mock` in a fenced code block with neither is exactly the primary-example
# drift this exists to catch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

python3 - "$ROOT" <<'PY'
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
files = sorted((root / "adapters").glob("*/README.md"))
if not files:
    sys.exit("no adapters/*/README.md files found")

FENCE = re.compile(r"```.*?```", re.DOTALL)
SMOKE = re.compile(r"smoke[- ]test", re.IGNORECASE)
BARE_MOCK = re.compile(r"(?<!\[)--mock(?!\])")
PARA_BREAK = re.compile(r"\n\s*\n")


def last_paragraph(chunk: str) -> str:
    parts = [p for p in PARA_BREAK.split(chunk) if p.strip()]
    return parts[-1] if parts else ""


violations = []
for path in files:
    text = path.read_text()
    fences = list(FENCE.finditer(text))
    for i, fence in enumerate(fences):
        code = fence.group(0)
        for m in BARE_MOCK.finditer(code):
            abs_pos = fence.start() + m.start()
            lineno = text.count("\n", 0, abs_pos) + 1
            line_start = code.rfind("\n", 0, m.start()) + 1
            line_end = code.find("\n", m.start())
            line = code[line_start : line_end if line_end != -1 else len(code)]

            # Only the paragraph immediately introducing this fence counts -- a
            # smoke-test sentence justifying some OTHER fence, or trailing prose
            # written after this one, must not excuse it just for being nearby.
            before_start = fences[i - 1].end() if i > 0 else 0
            before = last_paragraph(text[before_start : fence.start()])

            if SMOKE.search(before) or SMOKE.search(line):
                continue
            violations.append((path.relative_to(root), lineno, line.strip()))

if violations:
    print("adapter docs teach --mock as the default in a primary example:", file=sys.stderr)
    for path, lineno, line in violations:
        print(f"  {path}:{lineno}: {line}", file=sys.stderr)
    print(file=sys.stderr)
    print('Fix: drop --mock from the primary example, or say "smoke-test" in the', file=sys.stderr)
    print("paragraph immediately BEFORE the code block if it belongs there.", file=sys.stderr)
    sys.exit(1)

print(f"ok: no primary example in {len(files)} adapter docs defaults to --mock")
PY

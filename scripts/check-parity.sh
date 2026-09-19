#!/usr/bin/env bash
# Both runtimes must produce the same JudgmentResult for the same input.
# The shared rubrics are only "shared" if this holds.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export JUDGE_JEV_ROOT="$ROOT"
BIN="$ROOT/rust/target/release/judge-jev"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[[ -x "$BIN" ]] || (cd "$ROOT/rust" && cargo build --release --quiet)

CASES=(
  "assistant-reply:assistant-reply-pass"
  "assistant-reply:assistant-reply-skip"
  "assistant-reply:assistant-reply-injection"
  "assistant-reply:assistant-reply-escalate-flagged"
  "assistant-reply:assistant-reply-fail"
  "assistant-reply:assistant-reply-low-confidence"
  "agent-trajectory:agent-trajectory-pass"
)

failed=0
for case in "${CASES[@]}"; do
  rubric="${case%%:*}"
  fixture="${case##*:}"
  input="$ROOT/fixtures/$fixture.json"

  set +e
  (cd "$ROOT/python" && uv run judge-jev run --rubric "$rubric" --input "$input" --mock) \
    >"$TMP/py.json" 2>/dev/null
  py_exit=$?
  "$BIN" run --rubric "$rubric" --input "$input" --mock >"$TMP/rs.json" 2>/dev/null
  rs_exit=$?
  set -e

  if [[ "$py_exit" != "$rs_exit" ]]; then
    echo "FAIL $fixture: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
    failed=1
    continue
  fi

  if ! python3 - "$TMP/py.json" "$TMP/rs.json" "$fixture" <<'PY'
import json, sys

py_path, rs_path, fixture = sys.argv[1:4]
py = json.load(open(py_path))
rs = json.load(open(rs_path))
# request_id is transport metadata, not part of the judgment.
py.pop("request_id", None)
rs.pop("request_id", None)
py["deciding_answers"] = sorted(py["deciding_answers"])
rs["deciding_answers"] = sorted(rs["deciding_answers"])

if py == rs:
    print(f"ok   {fixture}: {py['verdict']} (confidence {py['confidence']:.4f})")
    sys.exit(0)

differing = sorted(k for k in set(py) | set(rs) if py.get(k) != rs.get(k))
print(f"FAIL {fixture}: runtimes differ on {differing}", file=sys.stderr)
for key in differing:
    print(f"  python {key}: {json.dumps(py.get(key), sort_keys=True)}", file=sys.stderr)
    print(f"  rust   {key}: {json.dumps(rs.get(key), sort_keys=True)}", file=sys.stderr)
sys.exit(1)
PY
  then
    failed=1
  fi
done

if [[ "$failed" != 0 ]]; then
  echo "runtime parity check failed" >&2
  exit 1
fi
echo "all runtimes agree"

#!/usr/bin/env bash
# Both runtimes must produce the same JudgmentResult for the same input.
# The shared rubrics are only "shared" if this holds.
#
# It used to compare successful mock `run` invocations and nothing else, which is
# how four divergences survived in the tree at once: a typo'd flag went live on one
# runtime and was a usage error on the other, usage errors exited 11 against 2,
# `replay` emitted a different shape for an absent `usage`, and the two disagreed on
# what `model` meant. Everything below exists because one of those got through.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export JUDGE_JEV_ROOT="$ROOT"
BIN="$ROOT/rust/target/release/judge-jev"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[[ -x "$BIN" ]] || (cd "$ROOT/rust" && cargo build --release --quiet)

py() { (cd "$ROOT/python" && uv run judge-jev "$@"); }
rs() { "$BIN" "$@"; }

failed=0

# Compare two saved JudgmentResults, ignoring only what is *meant* to differ.
compare_results() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys

py_path, rs_path, label = sys.argv[1:4]
py = json.load(open(py_path))
rs = json.load(open(rs_path))

# request_id is transport metadata, not part of the judgment.
py.pop("request_id", None)
rs.pop("request_id", None)

# `runtime` is the one field the two are supposed to disagree on — that is what it
# is for. Assert it rather than ignoring it, so a runtime that mislabels itself
# still fails here.
problems = []
for name, result in (("python", py), ("rust", rs)):
    stamped = result.pop("runtime", None)
    if not isinstance(stamped, dict) or stamped.get("name") != name:
        problems.append(f"{name} stamped runtime={stamped!r}")

py["deciding_answers"] = sorted(py["deciding_answers"])
rs["deciding_answers"] = sorted(rs["deciding_answers"])

differing = sorted(k for k in set(py) | set(rs) if py.get(k) != rs.get(k))
if not differing and not problems:
    print(f"ok   {label}: {py['verdict']} (confidence {py['confidence']:.4f})")
    sys.exit(0)

for problem in problems:
    print(f"FAIL {label}: {problem}", file=sys.stderr)
if differing:
    print(f"FAIL {label}: runtimes differ on {differing}", file=sys.stderr)
    for key in differing:
        print(f"  python {key}: {json.dumps(py.get(key), sort_keys=True)}", file=sys.stderr)
        print(f"  rust   {key}: {json.dumps(rs.get(key), sort_keys=True)}", file=sys.stderr)
sys.exit(1)
PY
}

# ---------------------------------------------------------------- run, per fixture
CASES=(
  "assistant-reply:assistant-reply-pass"
  "assistant-reply:assistant-reply-skip"
  "assistant-reply:assistant-reply-injection"
  "assistant-reply:assistant-reply-escalate-flagged"
  "assistant-reply:assistant-reply-fail"
  "assistant-reply:assistant-reply-low-confidence"
  "agent-trajectory:agent-trajectory-pass"
)

for case in "${CASES[@]}"; do
  rubric="${case%%:*}"
  fixture="${case##*:}"
  input="$ROOT/fixtures/$fixture.json"

  set +e
  py run --rubric "$rubric" --input "$input" --mock >"$TMP/py.json" 2>/dev/null
  py_exit=$?
  rs run --rubric "$rubric" --input "$input" --mock >"$TMP/rs.json" 2>/dev/null
  rs_exit=$?
  set -e

  if [[ "$py_exit" != "$rs_exit" ]]; then
    echo "FAIL $fixture: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
    failed=1
    continue
  fi
  compare_results "$TMP/py.json" "$TMP/rs.json" "$fixture" || failed=1
done

# ------------------------------------------------------------------- stdin (#7)
# `--input -` must reach the same verdict as the same bytes on disk.
STDIN_INPUT="$ROOT/fixtures/assistant-reply-pass.json"
set +e
py run --rubric assistant-reply --input - --mock <"$STDIN_INPUT" >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs run --rubric assistant-reply --input - --mock <"$STDIN_INPUT" >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL stdin-run: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "stdin-run" || failed=1
fi

# ------------------------------------------------------------------ replay (#6)
# Replay was never covered, which is how the usage/request_id shape drifted.
py run --rubric assistant-reply --input "$STDIN_INPUT" --mock >"$TMP/saved.json" 2>/dev/null

# (a) a full saved result, piped in
set +e
py replay --input - <"$TMP/saved.json" >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs replay --input - <"$TMP/saved.json" >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL stdin-replay: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "stdin-replay" || failed=1
fi

# (b) a hand-written case with no usage and no request_id. Python's asdict always
#     emitted those fields and Rust skipped them, so this exact input used to
#     produce two different shapes.
python3 - "$TMP/saved.json" "$TMP/trimmed.json" <<'PY'
import json, sys
saved = json.load(open(sys.argv[1]))
for key in ("usage", "request_id"):
    saved.pop(key, None)
json.dump(saved, open(sys.argv[2], "w"), indent=2)
PY
set +e
py replay --input "$TMP/trimmed.json" >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs replay --input "$TMP/trimmed.json" >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL replay-no-usage: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "replay-no-usage" || failed=1
fi

# (c) version drift must be refused identically, and allowed identically.
python3 - "$TMP/saved.json" "$TMP/drifted.json" <<'PY'
import json, sys
saved = json.load(open(sys.argv[1]))
saved["rubric_version"] = "0.0.0-not-the-one-on-disk"
json.dump(saved, open(sys.argv[2], "w"), indent=2)
PY
set +e
py_msg="$(py replay --input "$TMP/drifted.json" 2>&1 >/dev/null | tail -1)"
py replay --input "$TMP/drifted.json" >/dev/null 2>&1
py_exit=$?
rs_msg="$(rs replay --input "$TMP/drifted.json" 2>&1 >/dev/null | tail -1)"
rs replay --input "$TMP/drifted.json" >/dev/null 2>&1
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" || "$py_msg" != "$rs_msg" ]]; then
  echo "FAIL replay-drift: python=($py_exit) $py_msg" >&2
  echo "                  rust=($rs_exit) $rs_msg" >&2
  failed=1
else
  echo "ok   replay-drift: both refuse with exit $py_exit"
fi

set +e
py replay --input "$TMP/drifted.json" --allow-version-drift >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs replay --input "$TMP/drifted.json" --allow-version-drift >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL replay-drift-allowed: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "replay-drift-allowed" || failed=1
fi

# --------------------------------------------------------- malformed argv (#3)
# The half the old harness could not see: it only ever compared successful runs, so
# a typo that went live on one runtime and errored on the other looked identical.
# Message AND exit code, because 2 (argparse's default) is the `review` verdict.
ARGV_CASES=(
  "run --rubric assistant-reply --input FIXTURE --mok"
  "run --rubric assistant-reply --rubric agent-trajectory --input FIXTURE --mock"
  "run --input FIXTURE --mock --rubric"
  "run --input FIXTURE --mock"
  "rubric bogus"
  "bogus-command"
)

for raw in "${ARGV_CASES[@]}"; do
  # shellcheck disable=SC2206 - deliberate word splitting; these are argv vectors.
  args=(${raw//FIXTURE/$STDIN_INPUT})
  set +e
  py_msg="$(py "${args[@]}" 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
  py "${args[@]}" >/dev/null 2>&1
  py_exit=$?
  rs_msg="$(rs "${args[@]}" 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
  rs "${args[@]}" >/dev/null 2>&1
  rs_exit=$?
  set -e

  label="argv: ${raw//FIXTURE/<fixture>}"
  if [[ "$py_exit" != "$rs_exit" ]]; then
    echo "FAIL $label: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
    failed=1
  elif [[ "$py_msg" != "$rs_msg" ]]; then
    echo "FAIL $label: messages differ" >&2
    echo "  python: $py_msg" >&2
    echo "  rust:   $rs_msg" >&2
    failed=1
  elif [[ "$py_exit" != 11 ]]; then
    echo "FAIL $label: usage error exited $py_exit, expected 11" >&2
    failed=1
  else
    echo "ok   $label -> exit 11, same message"
  fi
done

# ------------------------------------------------------------------ --version
# Different text by design (it names the runtime), but both must succeed.
for runtime in py rs; do
  set +e
  "$runtime" --version >/dev/null 2>&1
  code=$?
  set -e
  if [[ "$code" != 0 ]]; then
    echo "FAIL --version: $runtime exited $code" >&2
    failed=1
  fi
done
echo "ok   --version: both exit 0"

# =============================================================== against a stub
# Everything above compares what came back. Nothing compared what went out, which
# is how the two runtimes shipped different request bytes for months: Python sent
# the filtered state in `state_filter` order and Rust sent it sorted, at every
# depth, and a harness that compares parsed results cannot see that at all.
#
# scripts/typesafe-stub.py stands in for the API so both can be pointed at it —
# Python through the SDK's TYPESAFE_BASE_URL, Rust by reading the same variable —
# and records the exact bytes of each request.
STUB="$ROOT/scripts/typesafe-stub.py"
JJ_ROOT="$ROOT"

stub_start() {
  local record="$1" statuses="$2"
  local portfile="$TMP/stub-port"
  : >"$portfile"
  python3 "$STUB" --record "$record" --statuses "$statuses" >"$portfile" 2>/dev/null &
  STUB_PID=$!
  for _ in $(seq 1 100); do
    STUB_PORT="$(awk '/^PORT/ {print $2; exit}' "$portfile" 2>/dev/null || true)"
    [[ -n "${STUB_PORT:-}" ]] && return 0
    sleep 0.1
  done
  echo "FAIL stub did not start" >&2
  return 1
}

stub_stop() {
  [[ -n "${STUB_PID:-}" ]] || return 0
  kill "$STUB_PID" 2>/dev/null || true
  wait "$STUB_PID" 2>/dev/null || true
  STUB_PID=""
}

# A live run, pointed at the stub. The key is a placeholder: the stub never looks
# at it, but both runtimes refuse to run live without one.
py_live() {
  (cd "$ROOT/python" && TYPESAFE_API_KEY=stub-key \
    TYPESAFE_BASE_URL="http://127.0.0.1:$STUB_PORT" \
    JUDGE_JEV_ROOT="$JJ_ROOT" uv run judge-jev "$@")
}
rs_live() {
  TYPESAFE_API_KEY=stub-key TYPESAFE_BASE_URL="http://127.0.0.1:$STUB_PORT" \
    JUDGE_JEV_ROOT="$JJ_ROOT" "$BIN" "$@"
}

# Run one judgment on each runtime against a fresh stub, leaving the recordings in
# $TMP/py-req.jsonl and $TMP/rs-req.jsonl and the results in $TMP/py.json/$TMP/rs.json.
run_both_against_stub() {
  local rubric="$1" input="$2" statuses="${3:-200}"
  rm -f "$TMP/py-req.jsonl" "$TMP/rs-req.jsonl"

  stub_start "$TMP/py-req.jsonl" "$statuses" || return 1
  set +e
  py_live run --rubric "$rubric" --input "$input" >"$TMP/py.json" 2>/dev/null
  PY_EXIT=$?
  set -e
  stub_stop

  stub_start "$TMP/rs-req.jsonl" "$statuses" || return 1
  set +e
  rs_live run --rubric "$rubric" --input "$input" >"$TMP/rs.json" 2>/dev/null
  RS_EXIT=$?
  set -e
  stub_stop
}

# The check the old harness could not make: the same bytes, not merely the same
# verdict derived from them.
compare_state_bytes() {
  python3 - "$TMP/py-req.jsonl" "$TMP/rs-req.jsonl" "$1" <<'PY'
import json, sys

py_path, rs_path, label = sys.argv[1:4]


def states(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line)["state_raw"] for line in fh if line.strip()]


py, rs = states(py_path), states(rs_path)
if not py or not rs:
    print(f"FAIL {label}: no request recorded (python={len(py)} rust={len(rs)})", file=sys.stderr)
    sys.exit(1)
if py[0] != rs[0]:
    print(f"FAIL {label}: the two runtimes sent different bytes", file=sys.stderr)
    print(f"  python {py[0]}", file=sys.stderr)
    print(f"  rust   {rs[0]}", file=sys.stderr)
    sys.exit(1)
print(f"ok   {label}: identical request state ({len(py[0])} bytes)")
PY
}

# ----------------------------------------------- the same bytes over the wire
# assistant-reply diverged at the top level ({prompt,reply,context} against
# {context,prompt,reply}); agent-trajectory diverged inside every step as well
# ({tool,input,output} against {input,output,tool}). Both are checked, because a
# canonical form that only sorts the top level would pass the first and fail the
# second.
for case in "assistant-reply:assistant-reply-pass" "agent-trajectory:agent-trajectory-pass"; do
  rubric="${case%%:*}"
  fixture="${case##*:}"
  run_both_against_stub "$rubric" "$ROOT/fixtures/$fixture.json" 200 || failed=1
  if [[ "$PY_EXIT" != "$RS_EXIT" ]]; then
    echo "FAIL wire-$fixture: exit codes differ (python=$PY_EXIT rust=$RS_EXIT)" >&2
    failed=1
    continue
  fi
  compare_state_bytes "wire-$fixture" || failed=1
  compare_results "$TMP/py.json" "$TMP/rs.json" "wire-$fixture" || failed=1
done

if [[ "$failed" != 0 ]]; then
  echo "runtime parity check failed" >&2
  exit 1
fi
echo "all runtimes agree"

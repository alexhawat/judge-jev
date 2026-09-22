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
# This harness asserts informational budget diagnostics, so a host-level filter
# such as RUST_LOG=warn must not make those contract checks disappear.
export RUST_LOG=judge_jev=info
BIN="$ROOT/rust/target/release/judge-jev"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Always build, never "build only if the binary is missing". A release binary left
# over from another branch is not missing, it is WRONG, and this harness compares it
# against a Python runtime that `uv run` always rebuilds from the current source. The
# failure mode is worse than a false FAIL: a stale binary that happens to agree
# reports "all runtimes agree" about code neither runtime is running. cargo no-ops
# when the tree is already up to date, so this costs nothing in CI.
(cd "$ROOT/rust" && cargo build --release --quiet)

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

# ----------------------------------------------------------- optional tracing
# No token: Python tracing and Rust's explicit no-op must preserve the contract.
for raw in "--tracing" "--tracing --tracing-to logfire" "--tracing-to logfire"; do
  read -r -a tracing_args <<< "$raw"
  set +e
  JUDGE_JEV_LOGFIRE_TOKEN= py run --rubric assistant-reply --input "$ROOT/fixtures/assistant-reply-pass.json" --mock "${tracing_args[@]}" >"$TMP/py.json" 2>/dev/null
  py_exit=$?
  rs run --rubric assistant-reply --input "$ROOT/fixtures/assistant-reply-pass.json" --mock "${tracing_args[@]}" >"$TMP/rs.json" 2>/dev/null
  rs_exit=$?
  set -e
  if [[ "$py_exit" != 0 || "$rs_exit" != 0 ]]; then
    echo "FAIL tracing $raw: expected pass (python=$py_exit rust=$rs_exit)" >&2
    failed=1
  else
    compare_results "$TMP/py.json" "$TMP/rs.json" "tracing $raw" || failed=1
  fi
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

# (d) Editing saved answers must recompute answer-derived diagnostics while the
#     input-only gates remain clearly identified as evidence from the original run.
python3 - "$TMP/saved.json" "$TMP/edited-answers.json" <<'PY'
import json, sys
saved = json.load(open(sys.argv[1]))
saved["answers"].pop("screen.injection")
json.dump(saved, open(sys.argv[2], "w"), indent=2)
PY
set +e
py replay --input "$TMP/edited-answers.json" >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs replay --input "$TMP/edited-answers.json" >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL replay-edited-answers: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "replay-edited-answers" || failed=1
  python3 - "$TMP/py.json" <<'PY' || failed=1
import json, sys
result = json.load(open(sys.argv[1]))
gates = {gate["gate_id"]: gate for gate in result["deterministic_gates"]}
assert gates["answer_completeness"]["outcome"] == "fail"
assert "screen.injection" in gates["answer_completeness"]["reason"]
assert all(gates[name]["reason"].startswith("historical evidence from original run: ")
           for name in ("state_projection", "token_budget", "injection_heuristic"))
PY
fi

# (e) Results saved before gate metadata existed remain replayable, but replay must
#     say that the input-only checks could not be rerun.
python3 - "$TMP/saved.json" "$TMP/legacy.json" <<'PY'
import json, sys
saved = json.load(open(sys.argv[1]))
saved.pop("deterministic_gates")
saved.pop("state_projection")
json.dump(saved, open(sys.argv[2], "w"), indent=2)
PY
set +e
py replay --input "$TMP/legacy.json" >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs replay --input "$TMP/legacy.json" >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL replay-legacy: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "replay-legacy" || failed=1
  python3 - "$TMP/py.json" <<'PY' || failed=1
import json, sys
gates = json.load(open(sys.argv[1]))["deterministic_gates"]
assert [gate["outcome"] for gate in gates[:3]] == ["skip", "skip", "skip"]
assert all("replay lacks raw state and historical" in gate["reason"] for gate in gates[:3])
PY
fi

# (f) A newer rubric with a stricter floor must replace the saved floor diagnostic,
#     not leave a historical pass next to a newly downgraded review verdict.
STRICT_ROOT="$TMP/strict-root"
mkdir -p "$STRICT_ROOT/shared/rubrics"
sed -e 's/version: "2.0.0"/version: "2.1.0"/' \
    -e 's/read_only: 0.5/read_only: 0.99/' \
    "$ROOT/shared/rubrics/assistant-reply.yaml" >"$STRICT_ROOT/shared/rubrics/assistant-reply.yaml"
export JUDGE_JEV_ROOT="$STRICT_ROOT"
set +e
py replay --input "$TMP/saved.json" --allow-version-drift >"$TMP/py.json" 2>/dev/null
py_exit=$?
rs replay --input "$TMP/saved.json" --allow-version-drift >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
export JUDGE_JEV_ROOT="$ROOT"
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL replay-stricter-floor: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "replay-stricter-floor" || failed=1
  python3 - "$TMP/py.json" <<'PY' || failed=1
import json, sys
result = json.load(open(sys.argv[1]))
gates = {gate["gate_id"]: gate for gate in result["deterministic_gates"]}
assert result["verdict"] == "review"
assert result["confidence_floor"] == 0.99
assert gates["confidence_floor"]["outcome"] == "fail"
assert "below 0.99 floor (downgraded pass to review)" in gates["confidence_floor"]["reason"]
assert "meets 0.50 floor" not in gates["confidence_floor"]["reason"]
PY
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
  "run --rubric assistant-reply --input FIXTURE --mock --tracing --tracing"
  "run --rubric assistant-reply --input FIXTURE --mock --tracing-to"
  "run --rubric assistant-reply --input FIXTURE --mock --tracing-to --tracing"
  "run --rubric assistant-reply --input FIXTURE --mock --tracing-to logfire --tracing-to logfire"
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
# An optional second argument is a string that must NOT appear in what was sent,
# which is how a projection is shown to have actually dropped something rather
# than merely reordering it.
compare_state_bytes() {
  python3 - "$TMP/py-req.jsonl" "$TMP/rs-req.jsonl" "$1" "${2:-}" <<'PY'
import json, sys

py_path, rs_path, label, forbidden = sys.argv[1:5]


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
if forbidden:
    leaked = [name for name, sent in (("python", py[0]), ("rust", rs[0])) if forbidden in sent]
    if leaked:
        print(f"FAIL {label}: {' and '.join(leaked)} sent {forbidden!r}, which no path selects",
              file=sys.stderr)
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

# --------------------------------------------- nested state_filter paths (#10)
# Each runtime unit-tests its own path semantics; only this proves the two select
# the same thing, in the same bytes, over the wire. The rubric lives under a
# temporary JUDGE_JEV_ROOT rather than in shared/rubrics, because `rubric list` is
# a user-visible surface and a parity fixture does not belong in it.
PATHS_ROOT="$TMP/paths-root"
mkdir -p "$PATHS_ROOT/shared/rubrics"

cat >"$PATHS_ROOT/shared/rubrics/parity-paths.yaml" <<'YAML'
id: parity-paths
version: "1.0.0"
description: Parity-only rubric exercising nested state_filter paths.
model: jev-1.13.0
stakes: read_only
confidence_floors:
  read_only: 0.5
state_filter:
  - goal
  - résumé
  - emoji.😀
  - steps[].tool
  - steps[].input
  - ticket.subject
  - final_output
questions:
  screen.judgeable:
    type: noul
    stage: screen
    instructions: "`steps` contains steps that can be judged against `goal`."
    criteria:
      "true": Steps are present to evaluate.
      "false": Missing steps.
routing:
  rules:
    - verdict: skip
      reason: Nothing judgeable here.
      all:
        - { answer: screen.judgeable, field: noul, op: "<", value: 0.5 }
    - verdict: review
      default: true
      reason: Parity rubric routes everything else to review.
YAML

# `required: true` on a path the input does not carry. Same rubric otherwise, so
# the only thing under test is what a missing required path does.
sed -e 's/^  - goal$/  - { path: "goal", required: true }\n  - { path: "reply", required: true }/' \
    -e 's/^id: parity-paths$/id: parity-required/' \
    "$PATHS_ROOT/shared/rubrics/parity-paths.yaml" >"$PATHS_ROOT/shared/rubrics/parity-required.yaml"

# Everything marked DROPPED is reachable in the input and excluded by the paths
# above: `steps[].output`, `ticket.body`, and a top-level key nothing selects.
cat >"$TMP/paths-input.json" <<'JSON'
{
  "goal": "find the readme",
  "résumé": "Unicode path",
  "emoji": { "😀": "supplementary Unicode path" },
  "steps": [
    { "tool": "glob", "input": "**/README.md", "output": "DROPPED-step-output" },
    { "tool": "read", "input": "README.md", "output": "DROPPED-step-output" }
  ],
  "ticket": { "subject": "card declined", "body": "DROPPED-ticket-body" },
  "final_output": "done",
  "unlisted": "DROPPED-unlisted-key"
}
JSON

JJ_ROOT="$PATHS_ROOT"
run_both_against_stub parity-paths "$TMP/paths-input.json" 200 || failed=1
if [[ "$PY_EXIT" != "$RS_EXIT" ]]; then
  echo "FAIL wire-paths: exit codes differ (python=$PY_EXIT rust=$RS_EXIT)" >&2
  failed=1
else
  compare_state_bytes "wire-paths" DROPPED || failed=1
fi

# A required path that is absent must fail the run identically, message and all —
# not warn on one runtime and warn on the other with different words.
# Both runtimes resolve rubrics through JUDGE_JEV_ROOT, so pointing it at the
# temporary root is enough to reach the parity rubric from either one.
export JUDGE_JEV_ROOT="$PATHS_ROOT"
set +e
py_msg="$(py run --rubric parity-required --input "$TMP/paths-input.json" --mock 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
py run --rubric parity-required --input "$TMP/paths-input.json" --mock >/dev/null 2>&1
py_exit=$?
rs_msg="$(rs run --rubric parity-required --input "$TMP/paths-input.json" --mock 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
rs run --rubric parity-required --input "$TMP/paths-input.json" --mock >/dev/null 2>&1
rs_exit=$?
set -e
export JUDGE_JEV_ROOT="$ROOT"
# Back to the real rubrics: everything after this judges with the shipped set.
JJ_ROOT="$ROOT"
if [[ "$py_exit" != "$rs_exit" || "$py_msg" != "$rs_msg" ]]; then
  echo "FAIL required-path: python=($py_exit) $py_msg" >&2
  echo "                   rust=($rs_exit) $rs_msg" >&2
  failed=1
elif [[ "$py_exit" != 10 ]]; then
  # 10, not a verdict: no judgment happened.
  echo "FAIL required-path: exited $py_exit, expected 10" >&2
  failed=1
else
  echo "ok   required-path: both exit 10, same message"
fi

# ----------------------------------------------------- the token budget (#8)
# The estimate has to be the same number on both runtimes, or the guard fires on
# one and not the other and the choice of runtime decides whether a judgment
# happens. Generated rather than committed: a 180KB fixture in the tree teaches
# nothing that this one line does not.
python3 - "$TMP/oversized.json" <<'PY'
import json, sys

# A plausibly long trajectory, not an artificially long one: 400 steps carrying
# tool output, which is a morning of agentic work.
steps = [{"tool": "read", "input": f"file-{i}.py", "output": "x" * 400} for i in range(400)]
json.dump({"goal": "refactor the parser", "steps": steps, "final_output": "done"},
          open(sys.argv[1], "w"))
PY

set +e
py_msg="$(py run --rubric agent-trajectory --input "$TMP/oversized.json" --mock 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
py run --rubric agent-trajectory --input "$TMP/oversized.json" --mock >/dev/null 2>&1
py_exit=$?
rs_msg="$(rs run --rubric agent-trajectory --input "$TMP/oversized.json" --mock 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
rs run --rubric agent-trajectory --input "$TMP/oversized.json" --mock >/dev/null 2>&1
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" || "$py_msg" != "$rs_msg" ]]; then
  echo "FAIL oversized: python=($py_exit) $py_msg" >&2
  echo "                rust=($rs_exit) $rs_msg" >&2
  failed=1
elif [[ "$py_exit" != 10 ]]; then
  # 10, not a verdict: the judgment did not happen.
  echo "FAIL oversized: exited $py_exit, expected 10" >&2
  failed=1
else
  echo "ok   oversized: both exit 10, same message"
fi

# The estimate itself, logged on every run. Comparing the message rather than the
# verdict is the point: two runtimes can agree on `pass` while disagreeing about
# how big the request was, and the next oversized input is where that shows up.
estimate_of() {
  "$@" run --rubric agent-trajectory --input "$ROOT/fixtures/agent-trajectory-pass.json" --mock 2>&1 >/dev/null \
    | grep -o 'token estimate .* of budget [0-9]*' | head -1
}
py_est="$(estimate_of py)"
rs_est="$(estimate_of rs)"
if [[ -z "$py_est" || "$py_est" != "$rs_est" ]]; then
  echo "FAIL token-estimate: python='$py_est' rust='$rs_est'" >&2
  failed=1
else
  echo "ok   token-estimate: both log '$py_est'"
fi

# The override, so the knob is known to work on both rather than only on the one
# whose docs mention it.
set +e
JUDGE_JEV_TOKEN_BUDGET=200000 py run --rubric agent-trajectory --input "$TMP/oversized.json" --mock >"$TMP/py.json" 2>/dev/null
py_exit=$?
JUDGE_JEV_TOKEN_BUDGET=200000 rs run --rubric agent-trajectory --input "$TMP/oversized.json" --mock >"$TMP/rs.json" 2>/dev/null
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" ]]; then
  echo "FAIL budget-override: exit codes differ (python=$py_exit rust=$rs_exit)" >&2
  failed=1
elif [[ "$py_exit" == 10 ]]; then
  echo "FAIL budget-override: still refused with the ceiling raised" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "budget-override" || failed=1
fi

# A ceiling that is not a positive integer is a usage mistake, and must not be
# silently read as zero or as the default.
set +e
py_msg="$(JUDGE_JEV_TOKEN_BUDGET=not-a-number py run --rubric assistant-reply --input "$STDIN_INPUT" --mock 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
JUDGE_JEV_TOKEN_BUDGET=not-a-number py run --rubric assistant-reply --input "$STDIN_INPUT" --mock >/dev/null 2>&1
py_exit=$?
rs_msg="$(JUDGE_JEV_TOKEN_BUDGET=not-a-number rs run --rubric assistant-reply --input "$STDIN_INPUT" --mock 2>&1 >/dev/null | grep '^judge-jev:' | head -1)"
JUDGE_JEV_TOKEN_BUDGET=not-a-number rs run --rubric assistant-reply --input "$STDIN_INPUT" --mock >/dev/null 2>&1
rs_exit=$?
set -e
if [[ "$py_exit" != "$rs_exit" || "$py_msg" != "$rs_msg" ]]; then
  echo "FAIL budget-malformed: python=($py_exit) $py_msg" >&2
  echo "                       rust=($rs_exit) $rs_msg" >&2
  failed=1
else
  echo "ok   budget-malformed: both exit $py_exit, same message"
fi

# ------------------------------------------------------------- retry (#9)
# The check that would have caught it: Rust issued one request and bailed on any
# non-2xx while Python retried through the SDK, so a 503 was transparent on one
# runtime and a hard failure on the other. Against a stub that fails twice and then
# succeeds, both must reach the same verdict — and must actually have retried,
# which is why the request count is asserted too. A case that only compared
# verdicts could pass with one runtime succeeding first try.
count_requests() { grep -c . "$1" 2>/dev/null || echo 0; }

run_both_against_stub assistant-reply "$STDIN_INPUT" 503,503,200 || failed=1
py_reqs="$(count_requests "$TMP/py-req.jsonl")"
rs_reqs="$(count_requests "$TMP/rs-req.jsonl")"
if [[ "$PY_EXIT" != "$RS_EXIT" ]]; then
  echo "FAIL retry-503: exit codes differ (python=$PY_EXIT rust=$RS_EXIT)" >&2
  failed=1
elif [[ "$py_reqs" != 3 || "$rs_reqs" != 3 ]]; then
  echo "FAIL retry-503: expected 3 requests each, got python=$py_reqs rust=$rs_reqs" >&2
  failed=1
else
  compare_results "$TMP/py.json" "$TMP/rs.json" "retry-503" || failed=1
  echo "ok   retry-503: both recovered after 2 failures, 3 requests each"
fi

# Exhausting the retries has to fail on both, after the same number of attempts.
# The message is not compared: the SDK words its own errors and this runtime words
# its own, and neither is wrong. The exit code and the attempt count are the
# contract.
export JUDGE_JEV_MAX_RETRIES=1
run_both_against_stub assistant-reply "$STDIN_INPUT" 503 || failed=1
py_reqs="$(count_requests "$TMP/py-req.jsonl")"
rs_reqs="$(count_requests "$TMP/rs-req.jsonl")"
unset JUDGE_JEV_MAX_RETRIES
if [[ "$PY_EXIT" != "$RS_EXIT" ]]; then
  echo "FAIL retry-exhausted: exit codes differ (python=$PY_EXIT rust=$RS_EXIT)" >&2
  failed=1
elif [[ "$PY_EXIT" != 10 ]]; then
  echo "FAIL retry-exhausted: exited $PY_EXIT, expected 10" >&2
  failed=1
elif [[ "$py_reqs" != 2 || "$rs_reqs" != 2 ]]; then
  # One initial attempt plus JUDGE_JEV_MAX_RETRIES=1, on both runtimes: the knob
  # has to reach the SDK's policy as well as this runtime's.
  echo "FAIL retry-exhausted: expected 2 requests each, got python=$py_reqs rust=$rs_reqs" >&2
  failed=1
else
  echo "ok   retry-exhausted: both exit 10 after 2 attempts"
fi

# A 4xx that is not 408 or 429 must not be retried by either: a malformed request
# will not fix itself, and retrying burns the budget before a real outage can use it.
run_both_against_stub assistant-reply "$STDIN_INPUT" 400 || failed=1
py_reqs="$(count_requests "$TMP/py-req.jsonl")"
rs_reqs="$(count_requests "$TMP/rs-req.jsonl")"
if [[ "$PY_EXIT" != "$RS_EXIT" ]]; then
  echo "FAIL no-retry-4xx: exit codes differ (python=$PY_EXIT rust=$RS_EXIT)" >&2
  failed=1
elif [[ "$py_reqs" != 1 || "$rs_reqs" != 1 ]]; then
  echo "FAIL no-retry-4xx: expected 1 request each, got python=$py_reqs rust=$rs_reqs" >&2
  failed=1
else
  echo "ok   no-retry-4xx: both gave up after 1 attempt"
fi

if [[ "$failed" != 0 ]]; then
  echo "runtime parity check failed" >&2
  exit 1
fi
echo "all runtimes agree"

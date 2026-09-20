# Shared plumbing for the examples. Sourced by each run.sh, never executed.
#
# It exists so every run.sh stays down to the part worth reading: the judge-jev
# invocation and what the caller does with the verdict.

EX_HERE="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
EX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JUDGE="$EX_ROOT/scripts/judge-jev"

# Mock is the default so every example runs offline, for free, and reaches the same
# verdict on every machine. Mock answers are canned: a mocked judgment is a test of
# the wiring, never a safety check, which is why the banner says so on every run.
EX_MODE=mock
for arg in "$@"; do
  case "$arg" in
    --live) EX_MODE=live ;;
    --mock) EX_MODE=mock ;;
    *)
      echo "usage: $(basename "${BASH_SOURCE[1]}") [--mock|--live]" >&2
      exit 11
      ;;
  esac
done

if [[ "$EX_MODE" == live ]]; then
  if [[ -z "${TYPESAFE_API_KEY:-}" ]]; then
    echo "--live needs TYPESAFE_API_KEY exported (drop --live to run mocked)" >&2
    exit 11
  fi
  EX_FLAGS=()
  echo "==> LIVE judgment: this calls the API and is billed" >&2
else
  EX_FLAGS=(--mock)
  echo "==> MOCK answers: offline, deterministic, and NOT a real safety check" >&2
fi

# Judge <rubric> <input.json>. Leaves the JSON in EX_JSON and the CLI's exit code in
# EX_CODE rather than exiting, so the caller can show what it does with each.
ex_judge() {
  set +e
  EX_JSON="$("$JUDGE" run --rubric "$1" --input "$2" "${EX_FLAGS[@]+"${EX_FLAGS[@]}"}")"
  EX_CODE=$?
  set -e
}

# Print the fields a caller actually acts on. The full JudgmentResult is on stdout
# from the CLI; this is the human-readable slice of it.
ex_summary() {
  printf '%s' "$EX_JSON" | python3 "$EX_ROOT/examples/_summary.py"
}

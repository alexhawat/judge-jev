#!/usr/bin/env bash
# Run every example and check it still reaches the verdict it claims to teach.
#
# Mock answers are deterministic, so these expectations are exact. That makes this
# a smoke test as well as a demo: if a rubric threshold moves, an example stops
# demonstrating what its README says and this fails instead of drifting quietly.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# example directory : expected exit code : what it demonstrates
CASES=(
  "01-judge-a-reply:0:a good reply passes"
  "02-judge-a-trajectory:0:a clean trajectory passes at write stakes"
  "03-gate-a-pipeline:0:a caller branches on the exit code"
  "04-replay-a-judgment:0:saved answers re-route offline"
  "05-prompt-injection:3:untrusted instructions escalate"
  "06-low-confidence:2:an unsure pass is downgraded to review"
)

failed=0
for case in "${CASES[@]}"; do
  dir="${case%%:*}"
  rest="${case#*:}"
  want="${rest%%:*}"
  what="${rest#*:}"

  set +e
  "$HERE/$dir/run.sh" >/dev/null 2>&1
  got=$?
  set -e

  if [[ "$got" == "$want" ]]; then
    printf 'ok   %-24s exit %s  %s\n' "$dir" "$got" "$what"
  else
    printf 'FAIL %-24s expected exit %s, got %s\n' "$dir" "$want" "$got" >&2
    failed=1
  fi
done

if [[ "$failed" != 0 ]]; then
  echo "examples are out of date with the rubrics" >&2
  exit 1
fi
echo "all examples behave as documented"

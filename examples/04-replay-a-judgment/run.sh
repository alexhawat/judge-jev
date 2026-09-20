#!/usr/bin/env bash
# Re-route a saved judgment without calling the API again.
#
# `replay` re-runs only the routing rules over answers that were already paid for.
# It is free and offline, which is what makes it the primitive for auditing a past
# decision and for testing a threshold change against saved answers.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_common.sh"

SAVED="$(mktemp)"
trap 'rm -f "$SAVED"' EXIT

# 1. Judge once and keep the result.
ex_judge assistant-reply "$EX_ROOT/examples/01-judge-a-reply/input.json"
printf '%s\n' "$EX_JSON" >"$SAVED"
echo
echo "1. judged and saved:"
ex_summary

# 2. Re-route the saved answers. No API call, no key, no cost.
echo
echo "2. replayed from the saved answers (no API call):"
set +e
EX_JSON="$("$JUDGE" replay --input "$SAVED")"
EX_CODE=$?
set -e
ex_summary
echo
echo "  exit code         $EX_CODE"

echo
echo "Note: replay routes against the rubric ON DISK TODAY, and the saved result"
echo "does not record which rubric version produced it. Edit a threshold in"
echo "shared/rubrics/assistant-reply.yaml and replay the same file to watch the"
echo "verdict change with no warning. That gap is issue #6."
exit "$EX_CODE"

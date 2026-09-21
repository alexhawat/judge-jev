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

# 3. Replay routes against the rubric ON DISK, so a saved judgment whose rubric
#    has moved since cannot quietly be re-decided under the new rules.
DRIFTED="$(mktemp)"
trap 'rm -f "$SAVED" "$DRIFTED"' EXIT
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d["rubric_version"]="1.0.0"; json.dump(d, open(sys.argv[2],"w"))' \
  "$SAVED" "$DRIFTED"

echo
echo "3. the same answers, saved under an older rubric version:"
set +e
DRIFT_MESSAGE="$("$JUDGE" replay --input "$DRIFTED" 2>&1 >/dev/null | tail -1)"
DRIFT_CODE=$?
set -e
echo "  exit $DRIFT_CODE (10 = the judgment did not happen)"
echo "  $DRIFT_MESSAGE"

echo
echo "Routing it anyway is a deliberate act — --allow-version-drift re-routes it and"
echo "records the drift in routing_reason, so an audit can still tell which rules"
echo "actually decided. Without that gate, editing a threshold in"
echo "shared/rubrics/assistant-reply.yaml would silently change what a past judgment"
echo "claims to have concluded."
exit "$EX_CODE"

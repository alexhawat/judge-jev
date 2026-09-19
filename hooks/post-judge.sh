#!/usr/bin/env bash
# Generic post-judge hook: read JudgmentResult JSON from stdin, map to an exit code.
# Mirrors the CLI contract so a pipeline can branch on the verdict the same way.
set -euo pipefail

RESULT="$(cat)"
VERDICT="$(printf '%s' "$RESULT" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin)["verdict"])
except Exception:
    # Not a JudgmentResult: report an operational error rather than a verdict.
    print("__error__")
')"

case "$VERDICT" in
  pass) exit 0 ;;
  fail) exit 1 ;;
  review) exit 2 ;;
  escalate) exit 3 ;;
  skip) exit 4 ;;
  __error__)
    echo "post-judge: stdin was not a JudgmentResult" >&2
    exit 10
    ;;
  *)
    echo "post-judge: unknown verdict '$VERDICT'" >&2
    exit 10
    ;;
esac

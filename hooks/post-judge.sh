#!/usr/bin/env bash
# Generic post-judge hook: read JudgmentResult JSON from stdin, map exit code.
set -euo pipefail
RESULT="$(cat)"
VERDICT="$(echo "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["verdict"])')"
case "$VERDICT" in
  pass) exit 0 ;;
  fail) exit 1 ;;
  review) exit 2 ;;
  escalate) exit 3 ;;
  skip) exit 4 ;;
  *) exit 2 ;;
esac

#!/usr/bin/env bash
# The smallest complete judgment: one prompt, one reply, one verdict.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_common.sh"

# assistant-reply asks eight questions in one call, then routes on the answers.
ex_judge assistant-reply "$EX_HERE/input.json"

echo
echo "judged: $EX_HERE/input.json"
ex_summary
echo
echo "  exit code         $EX_CODE   (0 pass, 1 fail, 2 review, 3 escalate, 4 skip)"
echo
echo "The full JudgmentResult is on stdout, so this composes:"
echo "  ./scripts/judge-jev run --rubric assistant-reply --input examples/01-judge-a-reply/input.json --mock | jq .answers"

# Exit with the verdict's code, the way a caller would.
exit "$EX_CODE"

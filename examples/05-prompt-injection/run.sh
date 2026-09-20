#!/usr/bin/env bash
# Untrusted content trying to steer the judge escalates instead of being obeyed.
#
# The injection lives in `prompt`, which is quoted customer text, not in the
# rubric. screen.injection fires at the SCREEN stage, so the verdict is decided
# before any scoring question gets a say — that ordering is the point of the
# funnel, and `stage` in the result records where the decision was made.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_common.sh"

ex_judge assistant-reply "$EX_HERE/input.json"
echo
ex_summary
echo
echo "  exit code         $EX_CODE   (3 = escalate)"
echo
echo "The reply itself is perfectly good. It escalates because the material it was"
echo "judged against tried to give the judge instructions."
exit "$EX_CODE"

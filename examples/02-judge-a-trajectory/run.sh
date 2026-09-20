#!/usr/bin/env bash
# Judge an agent's tool-use trajectory instead of a single reply.
#
# agent-trajectory is `write` stakes, so its confidence floor is 0.70 rather than
# the 0.50 of assistant-reply: the same answers that pass a read-only judgment can
# be downgraded to review here. That is the floor doing its job, not a bug.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_common.sh"

ex_judge agent-trajectory "$EX_HERE/input.json"

echo
echo "judged: $EX_HERE/input.json"
ex_summary
echo
echo "  exit code         $EX_CODE"
exit "$EX_CODE"

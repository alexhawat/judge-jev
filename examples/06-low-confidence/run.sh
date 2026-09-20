#!/usr/bin/env bash
# An automatic `pass` the judge was not sure enough about becomes `review`.
#
# The pass rule matches: helpfulness 3.0 and coherence 3.4 both clear their 2.0
# thresholds. But `confidence` is the MINIMUM over the answers that rule read, so
# the 0.40 on helpfulness decides it — under the read_only floor of 0.50 — and the
# verdict is downgraded, with the reason recording exactly why.
#
# Note which answers appear in `deciding answers`: only the two the pass rule read.
# The other six questions were answered and cost tokens, but they did not set the
# confidence, because they did not set the verdict.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_common.sh"

ex_judge assistant-reply "$EX_HERE/input.json"
echo
ex_summary
echo
echo "  exit code         $EX_CODE   (2 = review)"
echo
echo "Raise the floor and it happens sooner; lower it and this passes silently."
echo "The floor is confidence_floors[stakes] in shared/rubrics/assistant-reply.yaml."
exit "$EX_CODE"

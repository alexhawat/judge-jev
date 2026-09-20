#!/usr/bin/env bash
# Gate an action on the verdict. This is the shape every adapter and hook should
# copy: branch on the exit code, and keep operational failures out of the verdict
# branches.
#
# 0  pass      -> proceed
# 1  fail      -> block
# 2  review    -> proceed, but flag for a human
# 3  escalate  -> block and page a human
# 4  skip      -> nothing judgeable; proceed
# 10 error     -> the judgment DID NOT HAPPEN (bad input, no key, API down)
# 11 usage     -> the judgment DID NOT HAPPEN (the CLI was called wrong)
#
# 10 and 11 are the ones integrations get wrong. They are not a verdict of `fail`,
# and they are not a pass either: decide deliberately whether an unjudged action
# proceeds. This example fails closed for destructive work and open otherwise.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/_common.sh"

DESTRUCTIVE="${DESTRUCTIVE:-0}"

ex_judge assistant-reply "$EX_HERE/input.json"
echo
ex_summary
echo

case "$EX_CODE" in
  0) echo "-> pass: publishing the reply" ;;
  1) echo "-> fail: blocking the reply" >&2; exit 1 ;;
  2) echo "-> review: publishing, and queueing for a human" ;;
  3) echo "-> escalate: blocking and paging a human" >&2; exit 1 ;;
  4) echo "-> skip: nothing judgeable here, carrying on" ;;
  10 | 11)
    # Not a verdict. The content was never judged.
    echo "-> judge-jev could not judge (exit $EX_CODE)" >&2
    if [[ "$DESTRUCTIVE" == 1 ]]; then
      echo "-> destructive action, failing closed" >&2
      exit 1
    fi
    echo "-> non-destructive action, failing open" >&2
    ;;
  *) echo "-> unexpected exit $EX_CODE, treating as unjudged" >&2; exit 1 ;;
esac

echo
echo "Try the failure branch:  DESTRUCTIVE=1 $0 --live   # with no TYPESAFE_API_KEY set"

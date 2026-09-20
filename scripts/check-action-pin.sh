#!/usr/bin/env bash
# The mergeCraft Action pin is written twice in the same workflow: once as the
# `uses:` ref GitHub resolves, and once as MERGECRAFT_ACTION_SHA, which the
# container reads (resolve_action_pin_sha()) to record what it ran. Nothing at
# runtime notices when a bump touches one and not the other — the job still
# runs, and the run record attests to a commit that is not the one that ran.
# Upstream guards its own copy with `make action-pin-check`; a consumer install
# inherits none of that, so the rule lives here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Matched case-insensitively; GitHub resolves the repository either way.
ACTION="alexhawat/mergecraft"
ACTION_DISPLAY="alexhawat/mergeCraft"

failed=0
seen=0

for wf in "$ROOT"/.github/workflows/*.yml "$ROOT"/.github/workflows/*.yaml; do
  [[ -e "$wf" ]] || continue
  name="${wf#"$ROOT"/}"

  # Comment lines are prose about the pin (they quote both the old ref and the
  # image digest), so they are stripped before anything is read as a value.
  body="$(grep -vE '^[[:space:]]*#' "$wf" || true)"

  refs=()
  while IFS= read -r ref; do
    [[ -n "$ref" ]] && refs+=("$ref")
  done < <(
    printf '%s\n' "$body" \
      | grep -oiE "uses:[[:space:]]*$ACTION@[^[:space:]#]+" \
      | sed -E 's/.*@//'
  )
  [[ "${#refs[@]}" -gt 0 ]] || continue
  seen=1

  pins=()
  while IFS= read -r found; do
    [[ -n "$found" ]] && pins+=("$found")
  done < <(
    printf '%s\n' "$body" \
      | grep -oE '^[[:space:]]*MERGECRAFT_ACTION_SHA:[[:space:]]*[^[:space:]#]+' \
      | sed -E 's/^[^:]*:[[:space:]]*//' \
      | tr -d "\"'"
  )

  if [[ "${#pins[@]}" -eq 0 ]]; then
    echo "FAIL $name: uses $ACTION_DISPLAY but sets no MERGECRAFT_ACTION_SHA" >&2
    echo "  without it the container records an empty pin and cannot report a pin/image mismatch" >&2
    failed=1
    continue
  fi

  if [[ "${#pins[@]}" -gt 1 ]]; then
    for pin in "${pins[@]:1}"; do
      if [[ "$pin" != "${pins[0]}" ]]; then
        echo "FAIL $name: MERGECRAFT_ACTION_SHA set more than once with different values (${pins[0]}, $pin)" >&2
        failed=1
      fi
    done
  fi

  pin="${pins[0]}"
  drifted=0
  for ref in "${refs[@]}"; do
    if [[ "$ref" != "$pin" ]]; then
      echo "FAIL $name: uses $ACTION_DISPLAY@$ref but MERGECRAFT_ACTION_SHA is $pin" >&2
      drifted=1
      failed=1
    fi
  done

  if [[ "$drifted" == 0 ]]; then
    echo "ok   $name: ${#refs[@]} step(s) and MERGECRAFT_ACTION_SHA all pin $pin"
    # A tag can be moved to point at another commit; a full SHA cannot. Said out
    # loud rather than enforced, because the ref this repo runs is a maintainer's
    # call and a tag is a legitimate one.
    if [[ ! "$pin" =~ ^[0-9a-f]{40}$ ]]; then
      echo "     note: $pin is not a full 40-character commit SHA, so the ref can move under this repo"
    fi
  fi
done

if [[ "$seen" == 0 ]]; then
  echo "FAIL no workflow uses $ACTION_DISPLAY — this check has nothing to guard" >&2
  echo "  if the Action was removed on purpose, drop this check and its CI job too" >&2
  exit 1
fi

if [[ "$failed" != 0 ]]; then
  echo "action pin check failed" >&2
  exit 1
fi
echo "action pin is consistent"

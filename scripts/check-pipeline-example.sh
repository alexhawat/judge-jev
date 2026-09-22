#!/usr/bin/env bash
# Exercise every public exit class with a fake runner. No API calls are made.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FAKE="$TMP/fake judge-jev"

cat >"$FAKE" <<'SH'
#!/usr/bin/env bash
case "${FAKE_EXIT:?}" in
  0) verdict=pass ;;
  1) verdict=fail ;;
  2) verdict=review ;;
  3) verdict=escalate ;;
  4) verdict=skip ;;
  *) printf 'operational diagnostic, not JSON\n'; exit "$FAKE_EXIT" ;;
esac
printf '{"verdict":"%s","confidence":0.8,"confidence_floor":0.5,"stage":"route","deciding_answers":[],"routing_reason":"fake","model":"fake","mock":true,"usage":{"input_tokens":0,"output_tokens":0}}\n' "$verdict"
exit "$FAKE_EXIT"
SH
chmod +x "$FAKE"

check() {
  local judge_code="$1" destructive="$2" want="$3" got
  set +e
  FAKE_EXIT="$judge_code" DESTRUCTIVE="$destructive" JUDGE_JEV_EXAMPLE_RUNNER="$FAKE" \
    bash "$ROOT/examples/03-gate-a-pipeline/run.sh" --mock >/dev/null 2>&1
  got=$?
  set -e
  if [[ "$got" != "$want" ]]; then
    echo "pipeline fake exit=$judge_code destructive=$destructive: expected $want, got $got" >&2
    exit 1
  fi
}

check 0 0 0
check 1 0 1
check 2 0 0
check 3 0 1
check 4 0 0
check 10 0 0
check 11 0 0
check 10 1 1
check 11 1 1
check 12 0 1
echo "pipeline example handles verdict, operational, usage, and unexpected exits"

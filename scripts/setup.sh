#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export JUDGE_JEV_ROOT="$ROOT"

RUNTIME="${JUDGE_JEV_RUNTIME:-}"

if [[ -z "$RUNTIME" && -t 0 ]]; then
  echo "Select judge-jev runtime:"
  echo "  1) python"
  echo "  2) rust"
  read -r -p "Enter 1 or 2 [1]: " choice
  case "${choice:-1}" in 1) RUNTIME=python ;; 2) RUNTIME=rust ;; *) echo "Choose 1 or 2." >&2; exit 11 ;; esac
elif [[ -z "$RUNTIME" ]]; then
  RUNTIME=python
fi

case "$RUNTIME" in
  python)
    command -v uv >/dev/null || { echo "uv required; see https://docs.astral.sh/uv/"; exit 10; }
    set +e
    (cd "$ROOT/python" && uv sync --locked --dev)
    install_code=$?
    set -e
    [[ "$install_code" == 0 ]] || exit 10
    set +e
    "$ROOT/python/.venv/bin/judge-jev" --version >/dev/null
    smoke_code=$?
    set -e
    [[ "$smoke_code" == 0 ]] || exit 10
    ;;
  rust)
    command -v cargo >/dev/null || { echo "cargo required; install Rust toolchain"; exit 10; }
    set +e
    (cd "$ROOT/rust" && cargo build --release)
    install_code=$?
    set -e
    [[ "$install_code" == 0 ]] || exit 10
    set +e
    "$ROOT/rust/target/release/judge-jev" --version >/dev/null
    smoke_code=$?
    set -e
    [[ "$smoke_code" == 0 ]] || exit 10
    ;;
  *)
    echo "Unknown runtime: $RUNTIME (use python or rust)" >&2
    exit 11
    ;;
esac

mkdir -p "$ROOT/.judge-jev"
tmp="$(mktemp "$ROOT/.judge-jev/runtime.tmp.XXXXXX")" || exit 10
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$RUNTIME" > "$tmp" || exit 10
mv -f "$tmp" "$ROOT/.judge-jev/runtime" || exit 10
trap - EXIT

echo "Configured runtime=$RUNTIME at $ROOT/.judge-jev/runtime"
echo "Export TYPESAFE_API_KEY for live mode, or pass --mock to judge-jev run."

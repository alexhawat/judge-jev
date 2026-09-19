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
  RUNTIME=$([[ "${choice:-1}" == "2" ]] && echo rust || echo python)
elif [[ -z "$RUNTIME" ]]; then
  RUNTIME=python
fi

mkdir -p "$ROOT/.judge-jev"
echo "$RUNTIME" > "$ROOT/.judge-jev/runtime"

case "$RUNTIME" in
  python)
    command -v uv >/dev/null || { echo "uv required; see https://docs.astral.sh/uv/"; exit 1; }
    (cd "$ROOT/python" && uv sync --dev)
    ;;
  rust)
    command -v cargo >/dev/null || { echo "cargo required; install Rust toolchain"; exit 1; }
    (cd "$ROOT/rust" && cargo build --release)
    ;;
  *)
    echo "Unknown runtime: $RUNTIME (use python or rust)" >&2
    exit 1
    ;;
esac

echo "Configured runtime=$RUNTIME at $ROOT/.judge-jev/runtime"
echo "Export TYPESAFE_API_KEY for live mode, or pass --mock to judge-jev run."

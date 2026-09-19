#!/usr/bin/env bash
# Generic pre-judge hook: validate input JSON exists and runtime is configured.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="${1:-}"
RUBRIC="${2:-assistant-reply}"

[[ -n "$INPUT" && -f "$INPUT" ]] || { echo "usage: pre-judge.sh <input.json> [rubric]" >&2; exit 1; }
[[ -f "$ROOT/.judge-jev/runtime" ]] || { echo "run scripts/setup.sh first" >&2; exit 1; }
echo "pre-judge ok rubric=$RUBRIC input=$INPUT"

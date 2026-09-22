#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for name in assistant-reply.yaml agent-trajectory.yaml; do
  cmp "$ROOT/shared/rubrics/$name" "$ROOT/rust/assets/rubrics/$name"
done
for name in judgment-result.schema.json rubric.schema.json; do
  cmp "$ROOT/shared/schemas/$name" "$ROOT/rust/assets/schemas/$name"
done
echo "Rust packaged assets match shared rubrics and schemas"

# Cursor adapter

Cursor agents invoke the shared CLI via `scripts/judge-jev`.

## Install

1. Run `bash scripts/setup.sh`.
2. Add `skills/judge-jev` to your Cursor skills path or symlink.
3. Optional: merge `hooks/cursor/post-tool-use.json` into Cursor hook config when `JUDGE_JEV_AUTO=1`.

## Usage

```bash
export JUDGE_JEV_ROOT=/path/to/judge-jev
./scripts/judge-jev run --rubric assistant-reply --input fixtures/assistant-reply-pass.json --mock
```

Cloud agents should set `JUDGE_JEV_RUNTIME=python` for faster CI-style smoke tests.

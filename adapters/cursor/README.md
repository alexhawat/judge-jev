# Cursor adapter

Cursor agents invoke the shared CLI via `scripts/judge-jev`.

## Install

1. Run `bash scripts/setup.sh` (or `pwsh scripts/setup.ps1` on Windows).
2. Add `skills/judge-jev` to your Cursor skills path or symlink.
3. Optional: merge `hooks/cursor/post-tool-use.json` into Cursor hook config when
   `JUDGE_JEV_AUTO=1`. Set `JUDGE_JEV_INPUT` to the trajectory JSON itself, not a
   path -- the hook pipes it to `--input -`, so there is no temp file to manage.

## Usage

```bash
export JUDGE_JEV_ROOT=/path/to/judge-jev
./scripts/judge-jev run --rubric assistant-reply --input fixtures/assistant-reply-pass.json
```

Requires `TYPESAFE_API_KEY`. To smoke-test the wiring without a key, add `--mock`;
mock answers are canned and must never be reported as a real judgment.

Cloud agents should set `JUDGE_JEV_RUNTIME=python` for faster CI-style smoke tests.

## Windows

```powershell
$env:JUDGE_JEV_ROOT = "C:\path\to\judge-jev"
pwsh scripts/judge-jev.ps1 run --rubric assistant-reply --input fixtures/assistant-reply-pass.json
```

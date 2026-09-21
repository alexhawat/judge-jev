# OpenCode adapter

OpenCode can call judge-jev as an external command step.

## Template

```yaml
# .opencode/judge-step.yaml
name: judge-assistant-reply
run: bash -lc './scripts/judge-jev run --rubric assistant-reply --input $INPUT'
env:
  JUDGE_JEV_ROOT: .
```

Point `$INPUT` at a JSON file with `prompt`, `reply`, and optional `context` keys,
or at `-` to pipe the state from stdin instead. Requires `TYPESAFE_API_KEY` in the
step's environment.

To smoke-test the wiring without a key, add `--mock` to the `run:` line; mock
answers are canned and must never be reported as a real judgment.

## Windows

```yaml
# .opencode/judge-step.yaml
name: judge-assistant-reply
run: pwsh scripts/judge-jev.ps1 run --rubric assistant-reply --input $INPUT
env:
  JUDGE_JEV_ROOT: .
```

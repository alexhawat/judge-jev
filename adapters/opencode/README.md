# OpenCode adapter

OpenCode can call judge-jev as an external command step.

## Template

```yaml
# .opencode/judge-step.yaml
name: judge-assistant-reply
run: bash -lc './scripts/judge-jev run --rubric assistant-reply --input $INPUT --mock'
env:
  JUDGE_JEV_ROOT: .
```

Point `$INPUT` at a JSON file with `prompt`, `reply`, and optional `context` keys.

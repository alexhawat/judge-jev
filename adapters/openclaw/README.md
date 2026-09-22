# OpenClaw adapter (manual / experimental)

This is an unverified manual pattern, not a tested OpenClaw host contract or
enforcement gate. OpenClaw workflows can register a post-response judge step.

## Stub integration

```json
{
  "step": "judge-jev",
  "command": ["./scripts/judge-jev", "run", "--rubric", "assistant-reply", "--input", "${artifact_path}"]
}
```

Requires `TYPESAFE_API_KEY` in the step's environment. `${artifact_path}` can be
`-` to read the state from stdin instead of a file.

To smoke-test the wiring without a key, append `"--mock"` to the command array;
mock answers are canned and must never be reported as a real judgment.

## Windows

```json
{
  "step": "judge-jev",
  "command": ["pwsh", "scripts/judge-jev.ps1", "run", "--rubric", "assistant-reply", "--input", "${artifact_path}"]
}
```

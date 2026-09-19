# OpenClaw adapter

OpenClaw workflows can register a post-response judge step.

## Stub integration

```json
{
  "step": "judge-jev",
  "command": ["./scripts/judge-jev", "run", "--rubric", "assistant-reply", "--input", "${artifact_path}", "--mock"]
}
```

Replace `--mock` with live mode when `TYPESAFE_API_KEY` is configured.

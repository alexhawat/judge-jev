# Grok Bot adapter

Grok Bot integrations should call the repo CLI rather than embedding Jev prompts.

## Webhook-style stub

```bash
curl -sS -X POST "$JUDGE_JEV_WEBHOOK" \
  -H 'Content-Type: application/json' \
  -d @payload.json \
  || ./scripts/judge-jev run --rubric assistant-reply --input payload.json --mock
```

For local bots, invoke `./scripts/judge-jev` directly and forward stdout JSON to the bot channel.

# Grok Bot adapter

Grok Bot integrations should call the repo CLI rather than embedding Jev prompts.

## Gate the webhook on the verdict

The judgment has to run *before* the payload goes out, and its exit code has to
decide whether the webhook call happens. Chaining the two with `||` gets this
backwards: `curl ... || judge-jev run ...` only runs the judge when `curl` fails, so
the judgment becomes a fallback for a broken webhook rather than a gate on the
content -- and if that fallback is mocked, it never judges anything real either way.

Judge first, then branch on the exit code:

```bash
./scripts/judge-jev run --rubric assistant-reply --input payload.json >/tmp/verdict.json
code=$?

case "$code" in
  0|2|4)
    # pass, review, or nothing judgeable: send it. Flag `review` for a human
    # separately if your bot has a review queue.
    curl -sS -X POST "$JUDGE_JEV_WEBHOOK" -H 'Content-Type: application/json' -d @payload.json
    ;;
  1|3)
    echo "grok-bot: blocked by judge-jev (exit $code)" >&2
    exit 1
    ;;
  10|11)
    # Not a verdict -- the judgment did not happen. Decide deliberately; this
    # example fails closed rather than sending an unjudged payload.
    echo "grok-bot: could not judge (exit $code)" >&2
    exit 1
    ;;
esac
```

Requires `TYPESAFE_API_KEY`. To smoke-test the wiring without a key, add `--mock`
to the `judge-jev run` line above; mock answers are canned and must never gate a
real webhook call.

For local bots, invoke `./scripts/judge-jev` directly and forward stdout JSON to the
bot channel.

## Windows

```powershell
pwsh scripts/judge-jev.ps1 run --rubric assistant-reply --input payload.json
```

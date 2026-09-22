# Cursor adapter (manual / experimental)

Cursor is not the primary tested integration. Use the shared CLI manually:

```bash
./scripts/judge-jev run --rubric assistant-reply --input /absolute/path/to/input.json
```

This requires `TYPESAFE_API_KEY`. Add `--mock` only for a clearly labeled offline
plumbing test; canned mock answers do not measure quality.

The previous `post-tool-use.json` template has been removed. It ignored Cursor's
event stdin, repeatedly judged a static environment variable, and described exit
status 1 as enforcement even though that was not Cursor's contract.

Cursor's official [hooks documentation](https://prod.cursor.com/docs/hooks),
retrieved 22 September 2026, says command hooks receive JSON on stdin and return
JSON on stdout. Exit 2 blocks where the event is blockable; other nonzero exits
fail open by default. The current `stop` event receives only `status` and
`loop_count` and can return `followup_message`; it does not provide the completed
trajectory. A future complete adapter therefore needs a tested transcript/event
capture design rather than a copied Claude Stop command.

Project hooks live at `.cursor/hooks.json`; user hooks live at
`~/.cursor/hooks.json`. Do not treat this manual recipe as an installed safety gate.

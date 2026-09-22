# Claude Code integration

This is the primary, tested host integration. It evaluates one completed Claude
Code turn at the `Stop` checkpoint. It is a retrospective quality gate: it can ask
Claude to continue before the turn ends, but it cannot undo tool actions that
already completed.

The implementation follows the official [Claude Code hooks reference](https://code.claude.com/docs/en/hooks),
retrieved 22 September 2026. It reads Stop event JSON from stdin, uses
`last_assistant_message`, checks `stop_hook_active`, and returns the documented
top-level `decision: "block"` response. The installer uses exec-form commands with
absolute paths, avoiding shell quoting and checkout-directory assumptions.

## Preview, install, and remove

Complete repository setup first. Preview the exact settings merge:

```bash
python3 hooks/claude-code/claude_code_hook.py preview \
  --judge "$PWD/scripts/judge-jev"
```

Install into the default user settings file:

```bash
python3 hooks/claude-code/claude_code_hook.py install \
  --judge "$PWD/scripts/judge-jev"
```

Use `--settings /path/to/temporary/settings.json` to inspect or test another
file. The installer preserves unrelated settings and hooks, replaces only its own
tagged entry, writes atomically, and backs up an existing file. Repository tests
use temporary settings and never change the user's host configuration.

Remove only the owned entry:

```bash
python3 hooks/claude-code/claude_code_hook.py uninstall \
  --judge "$PWD/scripts/judge-jev"
```

## Offline doctor check

This uses the recorded Stop event and transcript plus `--mock`; it makes no model,
host, settings, or telemetry call:

```bash
python3 hooks/claude-code/claude_code_hook.py doctor \
  --event hooks/claude-code/fixtures/stop-event.json \
  --judge "$PWD/scripts/judge-jev"
```

Mock output proves event normalization and CLI wiring only. Live hook execution
requires `TYPESAFE_API_KEY` in Claude Code's environment.

## Behavior

The adapter reads the current user goal and tool uses/results from the event's
session transcript, combines them with `last_assistant_message`, and submits the
normalized `goal`, `steps`, `final_output` object to `agent-trajectory`.

| judge-jev outcome | Stop response |
|---|---|
| pass or skip | allow the turn to stop |
| fail, review, or escalate | block Stop once with the routing reason so Claude can respond |
| operational/usage error | allow by default and log to stderr |

When Claude invokes the hook again after hook feedback, `stop_hook_active` is true
and the adapter allows Stop. This prevents recursive stop loops. Set
`JUDGE_JEV_HOOK_FAILURE=closed` only when an unevaluated checkpoint should block;
the default is explicit fail-open because an outage is not a verdict.

The adapter does not create session-state files. Each event reads only its own
absolute transcript path, concurrent sessions stay isolated, and validated session
IDs are never interpolated into a filesystem path.

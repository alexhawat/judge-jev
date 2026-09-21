# Claude Code adapter

Thin wrapper around `./scripts/judge-jev` for Claude Code hooks.

## Install

1. Complete repo setup (`bash scripts/setup.sh`, or `pwsh scripts/setup.ps1` on Windows).
2. Copy `hooks/claude-code/settings-snippet.json` into your Claude Code settings `hooks` section.
3. Before a tool-heavy task, set `JUDGE_JEV_INPUT` to the trajectory JSON **itself**
   (the `goal`, `steps`, `final_output` object) -- not a path. `--input -` reads
   stdin, and the hook pipes `JUDGE_JEV_INPUT` straight into it, so there is no temp
   file to write or clean up.

## What the shipped hook does

`hooks/claude-code/settings-snippet.json` judges the trajectory **live** after every
tool use -- no `--mock` -- because a hook whose entire job is to gate real work has
no business judging canned answers. It blocks only on `escalate`, by exiting `2` so
Claude Code surfaces it; every other outcome is let through:

| judge-jev exit | Verdict  | Hook behavior                                             |
|-----------------|----------|------------------------------------------------------------|
| 0               | pass     | let through                                                 |
| 1               | fail     | let through (drop the `1)` arm from the `case` to block instead) |
| 2               | review   | let through                                                 |
| 3               | escalate | **blocked** -- hook exits 2                                 |
| 4               | skip     | let through                                                 |
| 10, 11          | not a verdict -- the judgment did not happen | let through, logged to stderr |

Requires `TYPESAFE_API_KEY` in the environment the hook runs in.

To smoke-test the wiring without a key or a real trajectory, call the CLI directly
with `--mock`:

```bash
echo '{"goal": "...", "steps": [], "final_output": "..."}' \
  | ./scripts/judge-jev run --rubric agent-trajectory --input - --mock
```

Mock answers are canned. That proves the plumbing works; it is never a real safety
check, and the shipped hook itself never runs mocked.

## Usage outside the hook

```bash
./scripts/judge-jev run --rubric agent-trajectory --input path/to/trajectory.json
```

## Windows

```powershell
pwsh scripts/judge-jev.ps1 run --rubric agent-trajectory --input path\to\trajectory.json
```

Same argument contract and exit codes as the bash dispatcher above.

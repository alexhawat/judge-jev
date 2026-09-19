# Claude Code adapter

Thin wrapper around `./scripts/judge-jev` for Claude Code hooks.

## Install

1. Complete repo setup (`bash scripts/setup.sh`).
2. Copy `hooks/claude-code/settings-snippet.json` into your Claude Code settings `hooks` section.
3. Set `JUDGE_JEV_INPUT` to a trajectory JSON path before tool-heavy tasks.

## Usage

```bash
./scripts/judge-jev run --rubric agent-trajectory --input path/to/trajectory.json --mock
```

The hook runs mock mode by default; remove `--mock` when `TYPESAFE_API_KEY` is available.

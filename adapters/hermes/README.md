# Hermes adapter

Hermes agents: install the skill at `skills/judge-jev/SKILL.md` and delegate judging to the CLI.

## Example tool manifest

```json
{
  "name": "judge_jev_run",
  "description": "Run shared rubric judgment",
  "parameters": {
    "rubric": { "type": "string" },
    "input_path": { "type": "string" },
    "mock": { "type": "boolean", "default": false }
  },
  "command": "./scripts/judge-jev run --rubric {rubric} --input {input_path} {mock_flag}"
}
```

`mock` defaults to `false` so the manifest judges for real; it requires
`TYPESAFE_API_KEY` in the agent's environment. Implement `{mock_flag}` as `--mock`
only when the caller explicitly asks to smoke-test the wiring, empty otherwise --
mock answers are canned and must never be reported as a real judgment.

`input_path` accepts `-` to read the state from stdin, so Hermes can pipe the JSON
it already holds instead of writing a temp file.

## Windows

Point the manifest's `command` at `pwsh scripts/judge-jev.ps1` in place of
`./scripts/judge-jev` when the Hermes host runs on Windows.

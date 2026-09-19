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
    "mock": { "type": "boolean", "default": true }
  },
  "command": "./scripts/judge-jev run --rubric {rubric} --input {input_path} {mock_flag}"
}
```

Implement `{mock_flag}` as `--mock` when true, empty otherwise.

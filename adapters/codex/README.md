# Codex adapter

Codex-style agents should treat `AGENTS.md` as setup instructions and shell out to the CLI.

## Command contract

```bash
./scripts/judge-jev run --rubric <id> --input <json> [--mock]
```

Parse stdout JSON (`JudgmentResult`) and map exit codes:

| Exit | Verdict   |
|------|-----------|
| 0    | pass      |
| 1    | fail      |
| 2    | review    |
| 3    | escalate  |
| 4    | skip      |

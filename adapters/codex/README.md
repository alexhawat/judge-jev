# Codex adapter

Codex-style agents should treat `AGENTS.md` as setup instructions and shell out to the CLI.

## Command contract

```bash
./scripts/judge-jev run --rubric <id> --input <json|-> [--mock]
```

`--input -` reads the state from stdin, so a Codex tool call can pipe the JSON it
already holds instead of writing a temp file.

Parse stdout JSON (`JudgmentResult`) and map exit codes. `10` and `11` are **not a
verdict** -- the judgment did not happen -- so they need a branch of their own; the
natural reading of "anything non-zero is a failure" turns an infrastructure hiccup
into a blocked action:

| Exit | Meaning                                                                  |
|------|---------------------------------------------------------------------------|
| 0    | pass                                                                       |
| 1    | fail                                                                       |
| 2    | review                                                                     |
| 3    | escalate                                                                   |
| 4    | skip                                                                       |
| 10   | **not a verdict** -- operational error (bad input, no API key, API down)  |
| 11   | **not a verdict** -- usage error (the CLI itself was called wrong)        |

For `10`/`11`, decide deliberately whether the unjudged action proceeds -- do not
read either as `fail`. `examples/03-gate-a-pipeline/run.sh` demonstrates exactly
this shape (it fails closed for destructive work and open otherwise, with the
reasoning in comments); copy it rather than reinventing the branch.

## Windows

Call `pwsh scripts/judge-jev.ps1` in place of `./scripts/judge-jev`; the argument
contract and exit codes above are identical.

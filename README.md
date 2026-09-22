# judge-jev



Jev-native judge kit for LLM outputs (assistant replies, agent trajectories). Dual production runtimes share rubrics, schemas, and CLI behavior.

## Agent-first setup

**Coding agents: read [AGENTS.md](AGENTS.md) and [llms.txt](llms.txt) before changing or running anything.**

Quick start:

```bash
git clone git@github.com:alexhawat/judge-jev.git
cd judge-jev
bash scripts/setup.sh                    # picks python or rust → .judge-jev/runtime
export TYPESAFE_API_KEY=...              # optional for live mode
./scripts/judge-jev run --rubric assistant-reply --input fixtures/assistant-reply-pass.json --mock
bash examples/run-all.sh                 # six end-to-end examples, mocked and offline
```

On Windows, use `pwsh scripts/setup.ps1` and `pwsh scripts/judge-jev.ps1` in place
of the bash scripts above — same runtime selection, same argument contract, same
exit codes.

## CLI

| Command | Description |
|---------|-------------|
| `judge-jev setup` | Choose/install runtime |
| `judge-jev run --rubric <id> --input <path\|-> [--mock]` | Run funnel → JSON `JudgmentResult` on stdout |
| `judge-jev rubric list\|show --id <id>` | Inspect shared rubrics |
| `judge-jev replay --input <result.json\|-> [--allow-version-drift]` | Re-route saved answers |
| `judge-jev --version` | Print the runtime and its version |

Exit codes — verdicts: `0` pass, `1` fail, `2` review, `3` escalate, `4` skip.
Operational failure (the judgment did not happen): `10` error, `11` usage. These are
kept clear of the verdict range so a crash can never be read as a verdict of `fail`.
JSON goes to stdout, logs to stderr, so `judge-jev run ... | jq` works.
A malformed command line is `11` in both runtimes, with the same message — never
`2`, which is the `review` verdict.

`--input -` reads stdin, so a hook can pipe the state it already has instead of
writing a temp file, and `run | replay` composes.

Use `./scripts/judge-jev` from repo root (dispatches via `.judge-jev/runtime`).
On Windows, use `pwsh scripts/judge-jev.ps1` — same args, same exit codes.

## Examples

Six runnable end-to-end examples live in [`examples/`](examples/): a good reply, an
agent trajectory, gating a pipeline on the exit code, replaying a saved judgment, a
prompt-injection escalation, and a confidence-floor downgrade.

```bash
bash examples/run-all.sh                      # all six, checked against expected verdicts
bash examples/01-judge-a-reply/run.sh         # one, with the result explained
bash examples/01-judge-a-reply/run.sh --live  # the same example against the real API
JUDGE_JEV_RUNTIME=rust bash examples/run-all.sh   # and the other runtime agrees
```

They are mocked by default, so they need no API key and cost nothing. Mock answers
are canned: a mocked judgment is a test of the wiring, never a safety check.
[`examples/README.md`](examples/README.md) explains what each one demonstrates and
how to judge your own input.

## Funnel

Each rubric implements: **screen → profile → locate → score → route**

- All questions fan out in one TypeSafe `system_one` call (pinned `jev-1.13.0`).
- Routing rules are **declarative data** in the rubric YAML, evaluated identically by
  both runtimes. No runtime interprets a rule as code.
- `--mock` provides deterministic CI-friendly answers without `TYPESAFE_API_KEY`.

### Routing rules

Rules run in order; the first whose `all` conditions hold wins. Conditions are ANDed
and short-circuit on the first false.

```yaml
- verdict: escalate
  reason: Possible prompt injection in untrusted state.
  all:
    - { answer: screen.injection, field: noul, op: ">=", value: 0.7 }
```

`field` is `noul`, `score`, `confidence`, or `choice`; `op` is `<  <=  >  >=  ==  !=`.
Rubrics are validated at load: a rule naming an unknown answer, an operator that does
not exist, a field the question's type cannot produce, or a choice value that is not a
declared label is rejected before any judgment runs.

A rule that *cannot* be evaluated — because an answer it reads is missing from the
response — escalates rather than being skipped, so a dropped answer can never let a
laxer rule decide the verdict.

### State filter

`state_filter` entries are paths, not just top-level keys: `a` selects a key, `a.b` a
nested key, `a[]` maps over a list, and `a[].b` projects a field from each element.
The filtered state keeps the original shape, so paths written in `instructions` still
resolve. An entry may instead be `{ path: ..., required: true }`; a path that does not
resolve is dropped with a warning unless it is required, which exits `10`.

In a YAML **inline** mapping the path must be quoted — `{ path: "steps[].tool",
required: true }` — because `[` and `]` are flow indicators. Bare paths and the block
mapping form need no quotes.

Every request is built from one canonical serialization: keys sorted at every depth,
compact separators, and `state` sent as exactly that text. Both runtimes must produce
identical bytes, and `scripts/check-parity.sh` asserts it.

### Answer semantics (Noul, Score, confidence)

See [`docs/judgment-contracts.md`](docs/judgment-contracts.md) for the full contract.

**Noul** values are `P(proposition yes)` on 0–1 — not a “% grounded” score. `0.5`
means **uncertain**, not “medium”.

**Score** answers are ordinal levels with API `legend`/`probabilities`; the number is
the chosen level, not a continuous quality metric.

**Verdict `confidence`** is the minimum over `deciding_answers` (answers the matched
rule read). For noul answers, confidence is derived as distance from 0.5. It is **not**
a probability that the verdict is correct. Answers the rule did not read never inflate it.

A routing rule whose answer is **missing** escalates — it never falls through to a laxer
rule or a silent pass.

An automatic `pass` or `fail` whose confidence falls below `confidence_floors[stakes]`
is downgraded to `review`. `review`, `escalate`, and `skip` are not gated.

### JudgmentResult contract fields

Every result includes `rubric_id`, `rubric_version`, resolved `model`, `state_projection`
(allowlisted paths + hash + projected keys), and `deterministic_gates` (code-run gate
outcomes). TypeSafe/API failures exit `10` and never emit a scored pass/fail verdict.

### Optional tracing (Python)

```bash
(cd python && uv sync --extra tracing)
export JUDGE_JEV_LOGFIRE_TOKEN=...          # EU by default (logfire-eu.pydantic.dev)
./scripts/judge-jev run --rubric assistant-reply --input fixtures/assistant-reply-pass.json --mock --tracing
```

Default **off**. The write token selects the Logfire project. Missing the extra or
token is a no-op. Rust accepts the same tracing flags as a no-op in v1.

## Layout

```
shared/rubrics/          # assistant-reply, agent-trajectory
shared/schemas/          # JudgmentResult JSON Schema
python/                  # uv + loguru + typesafe-sdk
rust/                    # cargo + tracing + HTTP client (POST /v1/systemone)
skills/judge-jev/        # agent skill
agents/                  # charters
hooks/                   # pre/post + harness snippets
adapters/                # claude-code, cursor, opencode, codex, openclaw, hermes, grok-bot
scripts/                 # setup, CLI dispatcher, runtime parity check
fixtures/                # smoke inputs (fixtures/recorded/ holds real API shapes)
examples/                # runnable end-to-end examples
```

## Runtimes

| Runtime | Stack | Live API |
|---------|-------|----------|
| Python | uv, loguru, typesafe-sdk | Official SDK |
| Rust | cargo, tracing, minreq | HTTP client matching Python wire format |

Both runtimes must produce the same `JudgmentResult` for the same input;
`scripts/check-parity.sh` enforces it in CI.

Set `JUDGE_JEV_RUNTIME=python|rust` or run setup interactively.

### Environment

| Variable | Effect |
|----------|--------|
| `JUDGE_JEV_RUNTIME` | `python` or `rust`, when `.judge-jev/runtime` is absent |
| `TYPESAFE_API_KEY` | Required for live judging |
| `JUDGE_JEV_TOKEN_BUDGET` | Ceiling, in estimated tokens, for one System One request (default `32000`). The estimate covers the filtered state and the rubric's questions together and is logged at INFO on every run. Exceeding it exits `10` before anything is sent, naming the largest contributing key. Must be a positive integer. |
| `JUDGE_JEV_MAX_RETRIES` | Retries after the initial attempt on a 408, 429, 5xx, connection or timeout error (default `2`, matching `typesafe-sdk`). `0` disables retries. Both runtimes read it; backoff is 0.5s doubling to a 5s cap with jitter, under a 30s total budget per call. |
| `JUDGE_JEV_LOGFIRE_TOKEN` | Optional Logfire write token (Python `[tracing]` extra; default region EU) |
| `JUDGE_JEV_LOGFIRE_REGION` | `eu` (default) or `us` — must match the token's region |

## Harness integration

Install the adapter README for your host under `adapters/`. Hooks under `hooks/` call the same CLI—no embedded Jev prompts in harness config.

## Development

```bash
# Python tests
cd python && uv sync --dev && uv run pytest

# Rust tests
cd rust && cargo test
```

## Jaggedness

Structured Jev judgments are fast and auditable, but accuracy is not uniform across tasks. Keep review/escalation paths for destructive stakes, low confidence, or untrusted inputs. Log `model`, `usage`, and answer probabilities for calibration.

## License

See [LICENSE](LICENSE).

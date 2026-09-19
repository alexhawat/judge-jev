# judge-jev

Private Jev-native judge kit for LLM outputs (assistant replies, agent trajectories). Dual production runtimes share rubrics, schemas, and CLI behavior.

## Agent-first setup

**Coding agents: read [AGENTS.md](AGENTS.md) and [llms.txt](llms.txt) before changing or running anything.**

Quick start:

```bash
git clone git@github.com:alexhawat/judge-jev.git
cd judge-jev
bash scripts/setup.sh                    # picks python or rust → .judge-jev/runtime
export TYPESAFE_API_KEY=...              # optional for live mode
./scripts/judge-jev run --rubric assistant-reply --input fixtures/assistant-reply-pass.json --mock
```

## CLI

| Command | Description |
|---------|-------------|
| `judge-jev setup` | Choose/install runtime |
| `judge-jev run --rubric <id> --input <path> [--mock]` | Run funnel → JSON `JudgmentResult` on stdout |
| `judge-jev rubric list\|show --id <id>` | Inspect shared rubrics |
| `judge-jev replay --input <result.json>` | Re-route saved answers |

Exit codes: `0` pass, `1` fail, `2` review, `3` escalate, `4` skip.

Use `./scripts/judge-jev` from repo root (dispatches via `.judge-jev/runtime`).

## Funnel

Each rubric implements: **screen → profile → locate → score → route**

- All questions fan out in one TypeSafe `system_one` call (pinned `jev-1.13.0`).
- Routing rules in rubric YAML execute in code against answers + confidence floors.
- `--mock` provides deterministic CI-friendly answers without `TYPESAFE_API_KEY`.

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
scripts/                 # setup + CLI dispatcher
fixtures/                # smoke inputs
```

## Runtimes

| Runtime | Stack | Live API |
|---------|-------|----------|
| Python | uv, loguru, typesafe-sdk | Official SDK |
| Rust | cargo, tracing, reqwest | HTTP client matching Python wire format |

Set `JUDGE_JEV_RUNTIME=python|rust` or run setup interactively.

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

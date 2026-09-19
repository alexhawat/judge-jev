# judge-jev skill

Use this skill when you need to **judge LLM outputs or agent trajectories** with TypeSafe Jev instead of prose-based LLM judges.

## When to use

- Scoring assistant replies against a rubric
- Evaluating agent trajectories (tool use, task success)
- Routing outputs to pass / fail / review / escalate with calibrated confidence

## Architecture (non-negotiable)

Follow [CHECKLIST.md](../../CHECKLIST.md) patterns baked into `@judge-jev/core`:

1. **One fan-out `systemOne` call** per judgment — all rubric questions in a single request.
2. **Pin `jev-1.13.0`** — never rely on `jev-latest` in production judges.
3. **Log `model` + `usage`** on every call.
4. **Filter state in code** before Jev — send only fields the rubric needs; strip untrusted instruction keys.
5. **Route in code** through stages: `screen → profile → locate → score → route`.
6. **Confidence floors by stakes** — read-only ~0.5, standard ~0.65, escalate ~0.8, destructive ~0.9.
7. **Noul 0.5 is a coin flip** — do not treat it as “medium”; use separate thresholds from Choice/Score confidence.
8. **Counts and dates in code** — precompute `tool_call_count`, timestamps, etc.; Jev judges only.

## CLI

```bash
npm run build
npm run judge-jev -- run --rubric assistant-reply --input fixtures/assistant-reply-pass.json --mock
npm run judge-jev -- rubric list
npm run judge-jev -- rubric validate
npm run judge-jev -- replay --fixture fixtures/replay-assistant-pass.json
```

Live calls require `TYPESAFE_API_KEY` and omit `--mock`.

## Rubrics

Versioned YAML packs live under `rubrics/<id>/v1.yaml`. Each file owns **questions, thresholds, confidence floors, and routes** together.

Available minimum packs:

- `assistant-reply` — single turn prompt + reply
- `agent-trajectory` — goal + trajectory summary

## Agent charters

See `agents/` for opinionated presets (`strict`, `product`, `safety`, `trajectory`).

## Jaggedness

Jev accuracy varies by task shape (“jaggedness”): some question types and state layouts work far better than others. When tuning rubrics:

- Prefer concrete evidence in state over long summaries
- Keep Choice menus aligned with live catalogs; filter stale options in code first
- Re-evaluate thresholds when pinning a new model version

Paraphrase TypeSafe’s jaggedness guidance when extending rubrics; verify API fields against the live SDK.

## Programmatic use

```typescript
import { judge, loadRubricFromFile, createMockJevClient, mockResult } from "@judge-jev/core";

const rubric = loadRubricFromFile("rubrics/assistant-reply/v1.yaml");
const client = createMockJevClient({ "*": mockResult({ /* answers */ }) });
const result = await judge({ rubric, state: { prompt, reply } }, { client });
console.log(result.route, result.reason);
```

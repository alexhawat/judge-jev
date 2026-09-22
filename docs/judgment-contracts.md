# Judgment contracts

Every `JudgmentResult` from `judge-jev run` or `replay` is a contract for downstream hooks, CI, and audit logs.

## Answer semantics

### Noul

A **noul** value is `P(proposition yes)` on a 0–1 scale. It is **not** a percentage of the reply that is grounded, cited, or correct.

- `0.5` means **uncertain**, not “medium quality”.
- Values near `0` or `1` mean the model leaned strongly no/yes.
- Routing compares noul with thresholds (`>= 0.7`, `< 0.5`, etc.) defined in rubric YAML.

### Score

A **score** is an ordinal level (with API `legend` and `probabilities`). The numeric score is the chosen level, not a continuous quality metric.

### Confidence (verdict-level)

`JudgmentResult.confidence` is the **minimum** over `deciding_answers` — the answers the matched routing rule actually read.

- For **choice** and **score** answers, confidence comes from the API field of the same name.
- For **noul** answers, confidence is derived as distance from 0.5: `abs(noul - 0.5) * 2`.
- This is **not** a probability that the final verdict is correct.
- Answers the rule did not read never inflate confidence.

Automatic `pass` / `fail` verdicts below `confidence_floors[stakes]` downgrade to `review`. The `confidence_floor` gate records `fail` for that downgrade.

A failed `injection_heuristic` gate escalates the verdict. It does not leave a scored `pass` or `fail` in place.
The heuristic is deterministic policy enforcement; its outcome is not a calibrated
model confidence or a probability that prompt injection occurred.

### Missing answers

If a routing rule needs an answer that is absent from the API response, the run **escalates** — it does not fall through to a laxer rule or silently pass.

## Result fields (contract)

| Field | Meaning |
|-------|---------|
| `rubric_id`, `rubric_version`, `rubric_hash` | Which rubric, version, and complete effective policy routed this result |
| `source_rubric_version`, `source_rubric_hash` | Policy under which replayed answers were originally collected |
| `model` | Resolved Jev model id (`jev-1.13.0` under `--mock`) |
| `state_projection` | Allowlisted `state_filter` paths, stable hash of the sorted path names, projected top-level keys |
| `deterministic_gates` | Code-run gates: `gate_id`, `outcome` (`pass`/`fail`/`skip`), `reason` |
| `usage` | Token counts when reported |
| `mock` | Whether `--mock` was used |

The projection hash is SHA-256 over the compact JSON array of sorted path names,
with non-ASCII code points escaped as JSON `\u` sequences. Its scope is the
allowlist only: projected values and the rest of the rubric are outside the hash.

The separate `rubric_hash` is SHA-256 over canonical JSON for all behavior-bearing
rubric content: model, stakes/floors, state-filter paths and required flags,
questions with instructions/criteria, and ordered routing rules. Replay refuses a
different hash even when the version string is unchanged unless the caller uses
`--allow-version-drift`. Results saved before this field existed remain readable;
their routing reason says content drift could not be checked.

`replay` recomputes `answer_completeness` and `confidence_floor` against the current
answers and rubric. It cannot rerun `state_projection`, `token_budget`, or
`injection_heuristic` without the original raw state, so saved outcomes for those
three are retained with a `historical evidence from original run` label. When a
saved result lacks that evidence, replay emits `skip` for the unavailable gate.

JSON integer values are preserved exactly in both runtimes, including values past
signed and unsigned 64-bit limits. Python uses unbounded integers and Rust enables
`serde_json` arbitrary precision. Finite JSON floats use the shared canonical
format. NaN and Infinity are rejected before any output or API call.

Python uses the pinned TypeSafe SDK retry policy: two retries by default, a
documented 30-second retry budget, and a 10-second per-operation timeout. The SDK
checks elapsed time before starting another retry; it does not shorten or preempt
an in-flight operation at the remaining-budget boundary. Rust uses the same retry
classes/backoff and additionally caps each request and sleep to a strict 30-second
monotonic deadline.

## Fail-closed

TypeSafe/API/timeout/invalid-response failures exit **`10`** (operational error). They never produce a scored `pass` or `fail` verdict on stdout.

Exit **`11`** is reserved for CLI usage errors, also distinct from verdicts.

## Optional tracing (Python only)

Install `uv sync --extra tracing` and set `JUDGE_JEV_LOGFIRE_TOKEN`. The write token selects the Logfire project and region. Set `JUDGE_JEV_LOGFIRE_REGION` to `eu` or `us` only to override that host. Run with `--tracing`, optionally adding `--tracing-to logfire`; `--tracing-to` alone does not enable tracing. Without the extra or token, tracing is a no-op. Rust accepts the same flags and validates the sink when `--tracing` is set, but does not emit Logfire spans in v1.

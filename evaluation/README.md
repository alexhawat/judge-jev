# Evaluation tooling

`evaluate.py` evaluates labeled JSONL cases against saved outputs by default. The
included corpus is a small synthetic, human-authored smoke set with ordinary,
failing, ambiguous, and adversarial cases for both rubrics. It checks pipeline and
policy mechanics; it is not evidence of real-world model accuracy.

Run the reproducible saved-fixture report:

```bash
cd python
uv run python ../evaluation/evaluate.py \
  --dataset ../evaluation/datasets/smoke.jsonl \
  --results ../evaluation/fixtures/smoke-saved-results.jsonl \
  --output ../evaluation/reports/smoke-report.json
```

The report contains overall agreement, a five-verdict confusion matrix,
per-verdict agreement/precision/recall/F1/support, macro F1, review burden, and missing/error
counts. Expensive expected `fail`/`escalate` to predicted `pass` cells are reported
separately with their denominator. Every frozen-redteam case must match its frozen
baseline; any changed verdict fails the command. Missing/error cases and incomplete
coverage also fail the command.
Optional `--ci-min-agreement`, `--ci-min-macro-f1`, and
`--ci-max-false-pass-rate` floors also return a nonzero status on regression.

When expected score labels are present, the report gives MAE, normalized MAE, and
fit (`1 - normalized MAE`), and nearest-level agreement. Nearest levels use
`floor(score + 0.5)` clamped to the rubric
range; fractional runtime scores remain valid. Diagnostics list disagreements,
deciding answers, confidence floors, explicit floor downgrades, rule-fire counts,
available rule margins, mean/median confidence, and token/call counts by rubric.
Latency and optional `cost_usd` are grouped by their explicit source; absent values
are reported as absent rather than invented. Cost is never estimated from tokens.

## Offline sweeps

`--sweep` validates and reroutes saved answers through the same routing and replay
gate service as the runtime, including historical deterministic injection evidence,
with in-memory threshold or confidence-floor variants. It performs no API call and
never writes rubric YAML:

```bash
uv run python ../evaluation/evaluate.py \
  --dataset ../evaluation/datasets/smoke.jsonl \
  --results ../evaluation/fixtures/smoke-saved-results.jsonl \
  --sweep 'assistant-reply|locate.hallucination_risk|noul=0.6,0.7,0.8' \
  --sweep 'assistant-reply|confidence_floor=0.4,0.5,0.6'
```

Sweeps report overall and per-verdict metrics, per-split support and metrics, false
passes, review burden, the red-team gate, stable rule IDs, and available rule
margins for each candidate. A threshold target used by more than one routing
condition is rejected as ambiguous. Sweeps are sensitivity analysis, not threshold
optimization. Each row is labeled `policy_only_reroute_of_saved_answers`: a sweep
reruns routing and deterministic replay gates over saved answers, not judge
instructions or a model. A target-rubric result without saved answers is counted as
missing/error instead of retaining its verdict from the original policy.

## Stable seams for future capture and backends

Dataset rows use the documented version 1 format below. Dataset-level format and
provenance metadata lives in a sibling `<dataset-stem>.meta.json`; without it,
reports say `dataset_kind: "unknown"` rather than guessing:

```json
{
  "id": "unique-case-id",
  "rubric_id": "assistant-reply",
  "split": "train | heldout | frozen-redteam",
  "category": "ordinary | failing | ambiguous | adversarial",
  "expected_verdict": "pass | fail | review | escalate | skip",
  "expected_scores": {"score.helpfulness": 3},
  "input": {"prompt": "...", "reply": "...", "context": {}}
}
```

For example, the included metadata declares `schema_version: 1`,
`dataset_kind: "synthetic-curated-smoke"`, label provenance, and intended use.

Result rows key any backend output to a case without changing the dataset:

```json
{
  "case_id": "unique-case-id",
  "provenance": "live | recorded | mock_pipeline | backend-name",
  "latency_ms": 123.4,
  "latency_source": "client_wall_clock",
  "result": {"verdict": "review", "answers": {}, "usage": {}},
  "error": "present instead of result when no judgment occurred"
}
```

Expected score labels must name actual score questions and use an integer rubric
level. Result confidences, scores, usage, and latencies are validated as finite and
in-domain before metrics are computed. Reports include separate labeled and
evaluated support per verdict and per split.

`baseline_verdict` may be added to a frozen-redteam case; it defaults to
`expected_verdict`. Every frozen-redteam case must be present and match that
baseline for the hard gate to pass. A dataset with no frozen-redteam support reports
the gate as unavailable, never passed.

This is the handoff seam for open issue #15 (capture), #16 (additional judge
backends), and #17 (optimization). This evaluator neither implements nor closes
those issues. It also does not implement result history; result files are explicit
inputs/outputs selected by the caller.

This synthetic smoke corpus does not supply the real-corpus evidence needed to
close issue #11.

`--run-mock` generates canned results and labels them `mock_pipeline`.
`--run-live` is the only mode that makes live calls and must be chosen explicitly;
it was not run while building this tooling. Use reviewed annotators, written label
guidance, adjudication, a heldout set untouched during policy development, and a
frozen adversarial set before publishing a quality baseline.

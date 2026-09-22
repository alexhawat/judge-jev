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
per-verdict precision/recall/F1/support, macro F1, review burden, and missing/error
counts. Expensive expected `fail`/`escalate` to predicted `pass` cells are reported
separately with their denominator. Frozen-redteam expensive false passes always
fail the command. Optional `--ci-min-macro-f1` and
`--ci-max-false-pass-rate` floors also return a nonzero status on regression.

When expected score labels are present, the report gives MAE, normalized MAE, and
nearest-level fit. Nearest levels use `floor(score + 0.5)` clamped to the rubric
range; fractional runtime scores remain valid. Diagnostics list disagreements,
deciding answers, confidence floors, explicit floor downgrades, rule-fire counts,
available rule margins, mean/median confidence, and token/call counts by rubric.
Latency is grouped by the result row's explicit `latency_source`; absent latency is
reported as absent rather than invented. Cost is not estimated from tokens because
no pricing contract is available in the saved result.

## Offline sweeps

`--sweep` reroutes saved answers with in-memory threshold or confidence-floor
variants. It performs no API call and never writes rubric YAML:

```bash
uv run python ../evaluation/evaluate.py \
  --dataset ../evaluation/datasets/smoke.jsonl \
  --results ../evaluation/fixtures/smoke-saved-results.jsonl \
  --sweep 'assistant-reply|locate.hallucination_risk|noul=0.6,0.7,0.8' \
  --sweep 'assistant-reply|confidence_floor=0.4,0.5,0.6'
```

Sweeps report agreement, macro F1, false passes, review burden, and the red-team
gate for each candidate. They are sensitivity analysis, not threshold optimization.

## Stable seams for future capture and backends

Dataset rows use schema version 1 by convention:

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

This is the handoff seam for open issue #15 (capture), #16 (additional judge
backends), and #17 (optimization). This evaluator neither implements nor closes
those issues. It also does not implement result history; result files are explicit
inputs/outputs selected by the caller.

`--run-mock` generates canned results and labels them `mock_pipeline`.
`--run-live` is the only mode that makes live calls and must be chosen explicitly;
it was not run while building this tooling. Use reviewed annotators, written label
guidance, adjudication, a heldout set untouched during policy development, and a
frozen adversarial set before publishing a quality baseline.

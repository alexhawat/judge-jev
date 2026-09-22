# Capture and rubric proposals

## Opt-in capture

Enable capture explicitly:

```bash
judge-jev run --rubric assistant-reply --input input.json --capture ./capture
# or
export JUDGE_JEV_CAPTURE=./capture
```

Capture is disabled by default. It writes versioned JSONL files with restrictive
permissions. Each record contains the filtered state after redaction, complete
answers and probability distributions, verdict evidence, requested/resolved model
provenance, rubric identity/hash, token usage, deterministic gate outcomes, and
margins for every evaluable routing rule.
It also stores a SHA-256 hash of the canonical unredacted filtered state, allowing a
new-input recorded replay to verify evaluated-content identity even when the stored
state was redacted. That hash is provenance rather than anonymization; low-entropy
content may still be guessable, so captures remain sensitive.
Categorical conditions are marked nonnumeric. Numeric margins are positive when a
condition is satisfied and negative when it is not; strict operators also record
whether equality failed. An AND rule's margin is the minimum of its numeric
conditions. Non-firing rules remain counterfactual evidence and are never described
as deciding the verdict.

Use repeatable `--redact` paths such as `customer.email` or `steps[].token`. Redaction
operates on a copy before serialization and supports nested arrays. It is an explicit
denylist: unlisted prompts, replies, tool output, and observations may contain private
content. Choose a restricted directory, redact secrets, set an organizational
retention period, and review access separately from application logs.

The policy records every applicable reason:

- an independent uniform audit slice (2% by default);
- low confidence and numeric boundary proximity;
- every confidence-floor downgrade and review/escalate result;
- a small, probability-gated per-rule stream with a per-process cap.

The independent `audit` designation is retained when enrichment also applies.
Enriched records are deliberately biased. Estimate population accuracy from the audit
stream alone, or from a sampling design with valid inclusion weights; do not treat the
combined file as an unbiased sample.

After the verdict, selection, serialization, record-size checks, and nonblocking
enqueue run synchronously with bounded memory. Directory creation, writes, rotation,
and retention enforcement run on the background writer. Oversize records and full
queues are dropped. Per-record, queue, file, directory, and retained-file limits bound
growth. Writers use exclusive per-process active files and atomically finalize them,
so retention never removes another process's open handle. Shutdown may wait for the
configured small, finite interval while the queue drains, then reports `written`,
`dropped`, and `errors` on stderr. Capture failure never changes the judgment or
stdout JSON.

Preview retention, then apply it:

```bash
judge-jev capture prune --dir ./capture --older-than 30d
judge-jev capture prune --dir ./capture --older-than 30d --apply
```

Only files owned by this capture format are eligible. Symlinks and unrelated files
are excluded.

### Preparing labeled evaluation rows

A capture is model evidence, not a ground-truth label and not a ready-made tuning
dataset. A reviewer must inspect the underlying example independently and assign the
label. If redaction removed context needed for a trustworthy decision, recover the
source through an authorized data workflow and verify its canonical hash against
`state_hash`, or exclude the example. Do not infer `expected_verdict` from the
captured verdict.

For each reviewed example, assign one stable ID and write a case row such as:

```json
{"id":"case-0042","rubric_id":"assistant-reply","split":"heldout","category":"factuality","expected_verdict":"review","expected_scores":{"score.helpfulness":2},"input":{"prompt":"...","reply":"...","context":{}}}
```

Choose `train`, `heldout`, or `frozen-redteam` before optimization, and keep the
frozen red-team split unchanged. `category`, `expected_verdict`, and optional
`expected_scores` come from the human review. Use the captured `state` as `input`
only when its redactions still leave a valid, reviewable case.

Write a separate result row with the same ID. Preserve the complete capture object
under `result`; evaluation and tuning read its judgment fields and ignore the
capture-only metadata:

```python
import json
from pathlib import Path

case_id = "case-0042"  # same independently assigned ID as the human-labeled case
capture_line = Path("capture/selected-record.jsonl").read_text(encoding="utf-8").splitlines()[0]
capture = json.loads(capture_line)
result_row = {
    "case_id": case_id,
    "provenance": "recorded_capture",
    "result": capture,
}
with Path("eval/results.jsonl").open("a", encoding="utf-8") as results_jsonl:
    results_jsonl.write(json.dumps(result_row, sort_keys=True) + "\n")
```

The captured verdict remains the model's baseline result; it is never the case label.
Add `cost_usd` only when it was calculated from an independently documented price,
and retain the original capture files as immutable provenance. Run evaluation corpus
validation before tuning so missing IDs, extra results, invalid splits, or incomplete
coverage fail explicitly.

## Numeric policy proposals

The Python frontend searches thresholds and confidence floors using recorded answers,
with zero API calls:

```bash
judge-jev tune thresholds \
  --rubric assistant-reply \
  --set eval/cases.jsonl \
  --results eval/results.jsonl
```

The versioned cost policy in `shared/tuning/cost-policy-v1.yaml` makes false passes far
more expensive than review burden. Search uses a seeded group-relative advantage
`(reward - mean) / std` to update a bounded sampling distribution. This is a CEM-like
policy search, not language-model reinforcement learning. It fits only the training
split, refuses insufficient per-class/per-split support, reports held-out metrics, and
rejects any frozen-red-team per-case regression regardless of aggregate reward. A
synthetic demo may bypass support checks to test mechanics, but its report forbids a
quality claim.

Output contains the seed, full cost policy, support and denominators, candidate
distributions, Pareto frontier, and a version-bumped unified YAML diff. The frontier
keeps safety recall, pass precision, human-review volume, tokens, and expected cost
visible. No winner is silently applied and the source rubric is never modified.

## GEPA instruction proposals

Install the pinned optional integration:

```bash
cd python
uv sync --locked --extra tuning
```

Dry-run planning performs no GEPA, model, or reflector calls:

```bash
judge-jev tune instructions --rubric assistant-reply \
  --question score.helpfulness --set eval/cases.jsonl --budget 300
```

Live mode must be explicit and choose a budget contract. The usable public contract
is call-count mode:

```bash
judge-jev tune instructions --rubric assistant-reply \
  --question score.helpfulness --set eval/cases.jsonl --live \
  --budget-mode calls --budget 300 --reflection-call-budget 20 \
  --reflection-model openai/gpt-5.1
```

The adapter reserves model-evaluation calls for a baseline and a bounded candidate
subset across train, held-out, and frozen-red-team splits before GEPA starts. It
checks the model and reflector counters before dispatch and disables retries, so one
authorized call cannot silently become several. Token usage and reflector cost are
reported when providers expose them, but they are measurements rather than caps.

The verified TypeSafe and Cloudflare Jev contracts do not currently expose a
provider-enforced output-token limit. Post-response usage cannot enforce a hard cap
because the spend has already happened, and GEPA observes reflector cost after a
request. `--budget-mode hard-spend` therefore refuses before the first provider,
reflector, or optimizer call. Supplying token/dollar flags in call-count mode also
fails instead of silently weakening the requested guarantee. The pinned GEPA path is
tested with offline evaluators and reflectors; no paid call was made here.

Only one question's instruction text is mutable. Question id, type, scale/criteria,
routing numerics, and all other questions remain fixed. Reflection examples contain
observed disagreements, deciding answers, and routing reasons rather than invented
explanations. The optional disk cache keys the complete question/criteria semantics,
canonical state hash, model, backend, and surrounding question context. It stores raw
model evidence and reroutes that evidence under the current numeric policy, so a
threshold change cannot reuse a stale cached verdict. Keys contain hashes rather than
state or credentials; values contain model answers, so the cache remains sensitive,
opt-in, atomic, corruption-recovering, bounded, and restricted to owned cache files.

GEPA emits bounded candidate reports with all three split metrics, its internal
training frontier, proposed version bumps, and unified diffs. Its train-selected
candidate is identified, but no winner is silently applied. Internal-frontier items
outside the configured gate-candidate bound are explicitly training-only evidence;
they have not passed independent split gates. It never edits the rubric. No
paid optimization was run to validate this implementation.

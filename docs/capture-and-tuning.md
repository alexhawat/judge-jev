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
provenance, rubric identity/hash, and margins for every evaluable routing rule.
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

Serialization, directory creation, writes, rotation, and retention enforcement happen
off the judgment path through a bounded nonblocking queue. Oversize records and full
queues are dropped. Per-record, queue, file, directory, and retained-file limits bound
growth. Writers use exclusive per-process active files and atomically finalize them,
so retention never removes another process's open handle. Shutdown waits for a small,
finite interval and reports `written`, `dropped`, and `errors` on stderr. Capture
failure never changes the judgment or stdout JSON.

Preview retention, then apply it:

```bash
judge-jev capture prune --dir ./capture --older-than 30d
judge-jev capture prune --dir ./capture --older-than 30d --apply
```

Only files owned by this capture format are eligible. Symlinks and unrelated files
are excluded.

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

Live mode must be explicit and supplies positive metric-call, task-token,
per-call-token, and reflection-cost caps plus a reflection model. The adapter reserves
the per-call token allowance before each request, runs sequentially, uses the backend's
bounded retries, and reserves budget for independent held-out and frozen-red-team
evaluation before GEPA starts. GEPA 0.1.4 supplies Pareto selection and enforces
`max_metric_calls` and `max_reflection_cost`.

Only one question's instruction text is mutable. Question id, type, scale/criteria,
routing numerics, and all other questions remain fixed. Reflection examples contain
observed disagreements, deciding answers, and routing reasons rather than invented
explanations. The optional disk cache keys the complete question/criteria semantics,
canonical state hash, model, backend, rubric, and surrounding question context. Keys
contain hashes rather than state or credentials; values contain model answers, so the
cache remains sensitive, opt-in, atomic, corruption-recovering, and bounded.

GEPA emits a proposed version bump and unified diff. It never edits the rubric. No
paid optimization was run to validate this implementation.

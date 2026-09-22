# Acceptance evidence for issues 15, 16, and 17

This matrix maps the issue acceptance criteria to implementation and automated
evidence. The implementation plan is in
[`implementation-plan-issues-15-17.md`](implementation-plan-issues-15-17.md), and
the primary-source contract review is in
[`research/jev-cloudflare-gepa-2026-09-22.md`](research/jev-cloudflare-gepa-2026-09-22.md).

## Issue 15: opt-in judgment capture

| Acceptance criterion | Implementation | Evidence |
|---|---|---|
| `run --capture <dir>` records policy-selected JSONL | Python and Rust funnel integration plus versioned capture builders and writers | Python backend/capture tests; Rust capture unit tests; public CLI smoke tests |
| Capture does not change a judgment, block enqueue, or grow without bounds | Bounded nonblocking queues, background-only filesystem setup, record/queue/file/directory/retention caps, finite shutdown, counted failure paths | queue-full, oversize, failed/slow writer, rotation, concurrent writer, setup failure, and shutdown tests |
| Complete provenance and counterfactual margins | Canonical rubric hash, rubric/runtime/backend/model provenance, redacted filtered state, full answers, deciding evidence, every rule and condition | record-schema validation and fixed-record fixture tests |
| Same canonical payload in both runtimes | Sorted canonical JSON with fixed timestamp/runtime fixture; dynamic timestamp and real runtime identity are the documented production differences | capture parity fixture asserted by Python and Rust and included in `scripts/check-parity.sh` |
| Redaction and retention are usable and documented | Recursive object/array redaction without input mutation; owned-file-only prune preview/apply; symlink rejection; privacy and sampling guidance | redaction/prune/symlink tests and `docs/capture-and-tuning.md` |

The enriched stream is intentionally biased. Only records whose reasons include
`audit` form the independent uniform sample; reports must not treat the combined
stream as a population estimate without valid inclusion weights.

## Issue 16: backend seam and Cloudflare Jev

| Acceptance criterion | Implementation | Evidence |
|---|---|---|
| Workers AI contract confirmed from a primary source | Cloudflare documents `typesafe/jev`, `POST /client/v4/accounts/{account}/ai/run`, the `{model,input:{state,questions}}` body, three question types, answers, model and usage | Dated research note with source URL; request/envelope stub tests |
| `typesafe`, `cloudflare`, and `replay` in both runtimes | Backend interface/trait, capability declaration and normalized response/provenance | resolution, capability, CLI and parity tests |
| Routing remains above the provider seam | Backends return normalized answers/usage/request identity; shared validation, routing, gates and result publication run afterward | invalid answer and local-gate tests; existing routing/parity suites |
| Retry and timeout policy stays inside each live backend | TypeSafe retains the shared policy; Cloudflare classifies status/transport failures, honors normalized retry delays, and uses bounded retry/deadline logic | local HTTP stub and deterministic clock/sleep tests; malformed successful JSON is fatal |
| Selected and resolved provider identity is recorded | Result and capture schemas carry backend, requested model, resolved model and live/recorded/demo provenance | schema, model and replay tests |
| Unknown or contradictory selection is a usage error before network | Explicit flag wins over environment and default; `--mock` is the canned replay alias and conflicts with live selection | Python/Rust CLI error-parity tests |

Cloudflare serves a moving alias. Because the rubric pins `jev-1.13.0`, the backend
rejects a response that resolves the alias to another version before publishing a
verdict. Cloudflare does not document a Jev-specific request-id field; `cf-ray` is
recorded when present.

## Issue 17: rubric proposals

| Requirement | Implementation | Evidence |
|---|---|---|
| Explicit asymmetric objective | Versioned confusion-cost policy with large false-pass costs plus separate review and token terms | known synthetic ranking and invalid-policy tests |
| Numeric search is free, bounded, reproducible and honestly named | Seeded, bounded group-relative CEM-like policy search over routing thresholds/floors; zero model calls | reproducibility, zero-variance, bounds and zero-API tests |
| Training data does not leak | Distribution updates use train rows only; held-out and frozen-red-team evaluation starts after search ends | label-perturbation test keeps every iteration/distribution byte-identical |
| Missing evidence cannot create a proposal | Exact case/result coverage, finite/domain validation, per-class/per-split support, known token usage and canonical rerouting are required | missing/extra/support/unknown-usage/invalid-answer tests |
| Independent safety gates | Packaged evaluation API reports every split and enforces exact per-case frozen-red-team baselines; held-out data is post-search evidence | fail-to-review, invalid/missing red-team evidence and no-leakage tests |
| Humans choose among explicit tradeoffs | Nondominated held-out frontier reports safety recall, pass precision, review volume, tokens and expected cost | Pareto dominance tests and complete candidate reports |
| Proposal-only governance | Version-bumped unified YAML diffs; originals are never modified | original-byte and proposed-YAML tests |
| Actual optional GEPA integration | Pinned `gepa==0.1.4`, concrete `GEPAAdapter`, declared asymmetric cost reward, real disagreement reflections, semantic raw-evidence cache and bounded candidate reports | actual installed GEPA offline optimize test plus public call-budget mode with fake evaluator/reflector and independent three-split gates |

No paid optimization was run, so the branch makes no claim that any proposed wording
or threshold improves live judge quality. The verified TypeSafe and Cloudflare Jev
contracts do not expose a provider-enforced output-token limit. Explicit live
call-count mode is usable: it reserves model and reflector calls before dispatch,
disables retries, and labels token/cost values as measurements. Strict token/dollar
mode refuses before any call rather than presenting post-hoc accounting as a hard
cap. Dry-run planning remains the default.

Track 2b, training a local open-weights judge or pre-filter, remains a separate
milestone. `local-judge-training.md` lists the data, privacy, GPU, evaluation,
artifact and operating prerequisites; this work produces no weights and makes no
training claim.

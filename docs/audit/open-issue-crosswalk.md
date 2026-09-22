# Open issues: relationship to the audit implementation

Checked all open issues and their comments on 22 September 2026. This is a scope/dependency record, not authorization to implement every separate feature proposal or close issues. Current implementation is Plan 001 contract corrections and Plan 002 complete audit/usability work.

**Later user authorization:** The user subsequently requested a separate executor/worktree/branch for #15, #16 and #17 in parallel. Their implementation is now covered by [Plan 004](004-production-capture-backends-and-tuning.md); they remain separate from the audit PR. The remaining-work columns below describe requirements for that executor, not a decision to omit them from the task. External real-model evidence, unverified Cloudflare availability and the issue's separate local-training milestone remain explicit limitations.

| Issue | Overlap and current action | Remaining work / closure condition |
|---|---|---|
| [#11: No calibration/eval harness](https://github.com/alexhawat/judge-jev/issues/11) | Build offline evaluator, threshold sweeps, metrics, split support and CI regression gate in Plan 002. Respect the issue's additional metric requirements from comments. Replay correctness and provenance are prerequisites supplied by 001/002. | Curated synthetic smoke cases are not a measured real-judgment corpus. Keep issue open unless all acceptance, including replayable labeled evidence and CI, is demonstrated. Real quality claims require human-reviewed actual model outputs. |
| [#15: Capture judgments in production](https://github.com/alexhawat/judge-jev/issues/15) | Opt-in result history, reliable provenance and rule explanations provide reusable foundations. Document the distinction; reference issue rather than close it. | Sampling independent of confidence, boundary/quota/non-pass enrichment, filtered-state redaction, bounded nonblocking background queue, per-process files/fork handling, rotation/retention, counters and cross-runtime record contract. History alone does not satisfy these. |
| [#16: Backend seam / Workers AI](https://github.com/alexhawat/judge-jev/issues/16) | Improve current validation/retry contracts without changing provider. Keep normalized result/provenance compatible with a future backend abstraction. | Confirm Jev availability/model ID and request/response/capability contract from a primary source before a Cloudflare implementation. Backend selection, normalized capabilities/replay provider and provider-owned retries are separate work; no guessed endpoint. |
| [#17: GEPA and GRPO-style optimization](https://github.com/alexhawat/judge-jev/issues/17) | Offline sweeps and evaluation metrics establish prerequisites. Keep proposed policy changes visible and versioned. | Depends on #11/#15. Needs asymmetric costs, adequate per-class support, frozen red-team constraints, three splits, proposal-only diffs/Pareto reporting; then numeric search, budgeted GEPA and a separate local-model milestone. A sweep is not completion of this issue. |

## Sequencing

1. Correct replay diagnostics/hash and input/routing validation; version/provenance/CLI fixes.
2. Deliver the overlapping #11 evaluator with explicit synthetic-versus-real evidence labels.
3. Implement #15 as its own production data acquisition feature, then grow and review the real labeled corpus.
4. Revisit #17 numeric optimization only when labels/support and independent regression gates are credible. Language optimization and model training require explicit budgets and environments.
5. #16 is independent of usability; availability/contract research precedes provider code.

## PR language

Use `Related to #11, #15, #16, #17` with exact supported capabilities. Use `Fixes`/`Closes` only for an issue whose complete acceptance is verified. No issue mutation or external comments are required for this crosswalk; the authorized PR bodies carry the references.

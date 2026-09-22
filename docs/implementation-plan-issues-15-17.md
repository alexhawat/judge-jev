# Plan 004: Implement issues 15, 16 and 17 in an isolated branch

## Authorization, status and dependencies

- User explicitly requested a separate agent, new worktree/branch, and parallel work on #15, #16 and #17 after the audit implementation started. This supersedes the earlier decision to leave their implementation for a later task.
- Priority P2; effort L; risk HIGH for data capture/provider boundaries and optimization claims.
- Planned at `c7991ad70513b987ee24f1215baadba2cf9db08d`, 22 September 2026. Issue bodies and comments were read live this date.
- Before dispatch PR #25 merged: start the new issue worktree at `689ff44f2a37f00d449638d383de6b42576243f8` instead. This includes `58bfb49` hash/injection/tracing corrections; inspect them as drift from the original plan. Final stacking still uses reviewed Plan 002.
- Source Git repo `/private/tmp/judge-jev-pr25-audit-20260922`, origin `alexhawat/judge-jev`.
- Worktree `/Users/alex/.codex/.chatgpt-projects/g-p-6ab22e922a588191b998828b3afa9e7c/worktrees/judge-jev-issues-15-17`; branch `codex/capture-backends-tuning`.
- Independent modules/research can start at the planned commit. Final integration depends on Plan 002 (`codex/audit-usability-complete`), which itself builds on Plan 001. Rebase onto the reviewed audit branch before PR so the stacked diff contains only this plan. Coordinate interfaces and files with `/root/execute_full_audit` and its evaluation worker.
- User authorizes implementation, commits and a PR in this task; publication follows parent review. No merge, force push, global installation, actual production capture, paid API/GEPA runs, credentials discovery/export or GPU training. Implement/test live pathways with fakes and explicit opt-in commands; do not claim real calibration or trained weights.
- Parent is read-only advisor, executor edits source. Parent maintains index. Executor may delegate bounded subwork when slots are available, but currently parallel audit workers are using the other slots.

## Current state and conventions

Read `AGENTS.md`, `llms.txt`, both manifests and existing tests. Python uses dataclasses, argparse/pytest and `JudgeJevError`; Rust uses serde/anyhow and integration tests. Shared YAML owns thresholds/questions. One batched System One request, model pinned by rubric, filter before network, JSON stdout and verdict/error codes remain contracts. Preserve `--mock` as a backward-compatible alias rather than breaking existing examples.

Python `typesafe_client.py::get_engine(mock,pinned)` returns `MockEngine` or `LiveEngine`; `LiveEngine.system_one` returns `(answers, usage, request_id, response.model)` and creates `TypeSafeClient(api_key=..., model=..., retry=..., timeout=...)`. Rust `typesafe.rs::LiveClient` reads TypeSafe key/base URL and owns retries around `POST /v1/systemone`; its `SystemOneResult` tuple has the same normalized concepts. Funnel modules currently choose mock/live directly. `JudgmentResult` already carries rubric/version, runtime, model, usage, answers, deciding answers, confidence/floor, projection and deterministic gates. Plan 002 adds complete rubric hash/provenance and stronger validation. Reuse these; no second incompatible result type.

Relevant sources: [#15](https://github.com/alexhawat/judge-jev/issues/15), [#16](https://github.com/alexhawat/judge-jev/issues/16), [#17](https://github.com/alexhawat/judge-jev/issues/17), and [#11](https://github.com/alexhawat/judge-jev/issues/11) including its metrics comment. Read the full current issue text/comments at execution start, then reconcile drift. In particular, #17 explicitly separates local-model fine-tuning as a later milestone; it is not a prerequisite to numeric search or GEPA tooling in this PR.

## Scope

Runtime modules/tests/manifests, shared schema/rubric extensions, CLI/launcher integration, eval/tuning/capture fixtures and tooling, CI/docs/plans. Start with new modules and tests to minimize overlap. Coordinate before changing Plan 002-owned funnel/routing/CLI/models. Do not change tuned production thresholds automatically, invent a Cloudflare API contract, silently broaden persisted data, or alter all adapters for this work.

## Step 1: Stabilize shared interfaces and research provider availability

Send parent the worktree/base and an interface proposal. Agree with audit executor on: normalized backend response; rubric hash and routing trace access; eval dataset/result/report functions including split labels and confusion costs; CLI extension points. Use a serializable `BackendCapabilities` describing supported question types and uncertainty semantics. Noul distance-from-0.5 and score/choice confidence are different statistics, neither a calibrated artifact-correctness probability. Document the current min-over-deciding-answers aggregation as a policy assumption.

Read current official TypeSafe and Cloudflare Workers AI docs/catalog. Find positive primary evidence for Jev model ID and the exact request/response/auth/question capabilities before adding a Cloudflare backend. If unavailable, implement the seam with TypeSafe/replay and explicitly leave Cloudflare unsupported, as #16 itself permits. Record sources/date and uncertainty; a failed search does not prove absence. No guessed endpoint and no credentialed availability probes.

**Research update, verified by parent on 22 September:** https://developers.cloudflare.com/ai/models/typesafe/jev/ now positively documents `typesafe/jev`, Noul/Choice/Score and usage/model/answer response fields. Its REST example uses `POST /client/v4/accounts/{account}/ai/run` with `{ "model": "typesafe/jev", "input": { "state": ..., "questions": ... } }`, unlike the older URL-shaped guess in #16. It shows resolved `jev-1.13.0` but does not by itself establish arbitrary version pinning. Implement Cloudflare using this actual contract after checking its envelope/error/auth details and document alias-versus-version provenance; do not silently claim a provider alias guarantees the pinned model. Official GEPA references: https://gepa-ai.github.io/gepa/api/core/optimize/ and https://gepa-ai.github.io/gepa/api/core/GEPAAdapter/ (optional adapter, max_metric_calls, max_reflection_cost, callbacks and Pareto selection are documented).

Verification: saved contract/research note with source URLs; backend capability unit tests. No network judging.

## Step 2: Implement backend selection in both runtimes (#16)

Add backend interface/trait with id, capabilities and one normalized `system_one` operation. Default `typesafe`; `replay` accepts saved/captured answers or the existing canned mock mode, with provenance distinguishing canned demo from recorded model evidence. `--backend` and `JUDGE_JEV_BACKEND` use explicit flag > environment > default. Unknown value is usage 11 before credentials/network. Contradictory `--mock`/live backend selections must error clearly, never unexpectedly call the API. Record selected backend in results/schema and preserve legacy result loading.

Keep filtering, budgets, answer validation, routing and gates above the seam. TypeSafe owns current retry/timeout and explicit endpoint config. Replay makes zero HTTP requests and reports original model/usage provenance accurately; replay usage is historical, not newly billed. Preserve distinction between re-routing a saved result and judging newly supplied input from recorded answers; validate compatibility rather than presenting mismatched recordings as fresh evidence. Unsupported capabilities fail before request.

Verification: same fixtures across TypeSafe stub/replay produce same normalized routing; live HTTP stub envelope/error/retry tests; model echoed provenance; flags/env/conflicts and unknown choices in Python/Rust parity; legacy `--mock` behavior intact. Cloudflare tests only against verified documented envelopes if included.

## Step 3: Build opt-in bounded production capture (#15)

Implement `run --capture <dir>` / `JUDGE_JEV_CAPTURE` and documented policy configuration. Capture only when enabled. Record version, timestamp, trigger reasons, original rubric/version/hash/runtime/backend/model, filtered state AFTER redaction, full answers/probabilities, verdict/confidence/floor/deciding answers, routing reason and numeric rule margins. Reuse result provenance. Margins should cover evaluable non-firing numeric conditions without changing routing or claiming short-circuited conditions decided the verdict; categorical conditions are explicit nonnumeric/not-applicable. Define AND-rule margins and direction for each supported operator, especially strict boundaries/equality.

Policy streams: configurable independent uniform audit slice (default 2%), low-confidence enrichment, numeric boundary proximity, every floor downgrade, bounded per-rule quota and review/escalate enrichment. Record every applicable reason; do not erase the independent audit sample designation when enrichment also applies. Deterministic injectable RNG for tests only; production randomness independent of verdict/confidence. Explain that enriched combined data are biased; unbiased estimates use the audit stream or valid weighting.

Hot-path requirements:

- Bounded queue with nonblocking enqueue; oversize serialization/records rejected before enqueue; failures swallowed and counters incremented. Capturing must never change verdict or raise into judging.
- A background writer, exclusively created per-process-instance files, no shared append. Fork children reset writer/queue/PID identity; no inherited lock deadlock.
- Per-record, queue, file and directory retention/storage bounds. Rotate by size and enforce documented cap; symlink/path handling must not overwrite arbitrary files. Restrictive directory/file permissions where supported.
- Finite shutdown policy; do not wait indefinitely for slow storage. Count pending dropped records and expose written/dropped/error counters to stderr/doctor, never mix logs with stdout JSON.
- Explicit redaction paths/keys applied before capture serialization and without mutating judge input; support nested objects/arrays and required secret-field removal. Document what remains and that prompts/tool output may contain private content.
- `capture prune --older-than` with preview/dry-run, only owned capture files, clear retention and deletion semantics; no deletion of unrelated paths.

The issue asks for byte-identical records while also requiring real runtime identity and timestamps. Define canonical JSON equivalence for the shared payload; fixture-inject time/runtime identity to compare full bytes, and separately verify real runtime identity is correct. Do not fake equal runtime names just to satisfy a diff. Document unavoidable varying metadata explicitly.

Verification: table-driven sampling with fixed RNG; queue full and writer/permission failures never alter judgment; huge payload rejection; bounded file/directory growth; per-process/exclusive names/concurrency/fork behavior where supported; redaction before persistence and no raw secret in any file/log; retention only owned files; canonical cross-runtime record parity under fixed metadata. Use temporary directories and fabricated data only.

## Step 4: Numeric proposal search and evaluation safeguards (#17 track 2a)

Consume Plan 002 evaluation APIs/corpus format; do not build a competing metric engine. Add rubric-declared confusion costs or a versioned shared cost policy, distinguishing false passes from review burden, with explicit human-readable defaults. Preserve unknown/custom metadata compatibility in loaders and include all effective policy in rubric hash. Score candidates with negative expected asymmetric cost and explicit review/token terms; report confusion details, support and costs rather than aggregate agreement alone.

`tune thresholds --rubric ... --set ...` defaults to proposal-only and performs zero API calls. Candidate vector may contain declared numeric thresholds/confidence floors; preserve valid ranges/order constraints and derive bounded search space explicitly. Use seeded group-relative advantage `(reward-mean)/std`, safe zero-variance handling, and a reproducible sampling-distribution update. Describe honestly as group-relative policy search/CEM-like optimization, not language-model reinforcement learning. Share sweep/sensitivity code where reliable; optimization need not claim analytic no-replay updates for multi-threshold changes.

Require train/held-out/frozen-red-team splits and configurable minimum class support. Refuse a quality proposal when support is insufficient; permit an explicitly labeled synthetic demo for testing mechanics only. Red-team regression is a hard rejection independent of aggregate score. Do not fit on held-out/red-team labels: use train to search, report independent splits and reject on fixed constraints. Return a Pareto frontier across safety recall, precision, human-review volume and token/cost measures with denominators and unavailable metrics; do not silently pick/apply a safety tradeoff.

Output self-contained report + unified rubric diff, candidate provenance/seed and proposed version bump. Write no source rubric changes automatically. Applying a proposal remains a normal reviewed change; automatic acceptance or merge is out of scope. Bound iterations/candidates and fail usefully on invalid constraints.

Verification: known small labeled synthetic corpus, deterministic candidates/reports, zero-network assertion, negative-cost ranking, insufficient-support refusal, zero variance, train leakage checks, held-out/red-team regression rejects apparent aggregate winner, Pareto dominance, original rubric byte-identical after every run, proposal diff loads and cross-runtime routing agrees.

## Step 5: Budgeted GEPA instruction proposals (#17 track 1)

Research current official GEPA API/docs and use an optional pinned integration rather than labeling an unrelated custom algorithm GEPA. Add `tune instructions --rubric ... --question ... --budget ...` for instructions/criteria wording only. Preserve question ID/type/score-scale semantics and keep numerical policy fixed during language optimization. GEPA reflection receives discrepancies and evidence from labeled data, not fabricated explanations. Isolate candidate evaluation from arbitrary tool execution.

Live execution requires explicit mode, positive metric-call and spend/token limits as supported; default remains dry-run/planning with no API. Count initial/candidate/reflector calls within documented separate or total budgets, abort before exceeding caps, and bound concurrency/retries. Cache using full question/criteria semantics, canonical state hash, model/backend and relevant rubric/context to prevent stale cache reuse. Atomic/cache corruption recovery, no secret values in keys/reports. Keep sensitive state cache opt-in/documented and bounded; do not inadvertently create an unredacted production store.

Use stratified training samples, independent held-out/red-team gates, minimum support and Pareto reporting from Step 4. Output a proposed versioned unified diff/report, never apply. Optional-dependency absence gives actionable setup guidance. Unit/integration tests use fake GEPA/TypeSafe/reflector clients, assert budget consumption and no output outside temporary locations. No actual paid optimization is run in this task.

Track 2b local judge fine-tuning is explicitly a separate milestone in #17: document data/training/hardware prerequisites and do not ship fake weights or claim trained-model functionality. If issue closure would require that milestone, leave #17 open with precise delivered/remaining checklist.

## Integration, checks and delivery

Add docs, examples and CI tests for all three systems; preserve beginner quickstart and keep advanced controls discoverable through help. Add a committed acceptance matrix quoting each issue acceptance bullet with code/test evidence and remaining external limitations. Include this plan and research note. Scope drift due to a verified provider contract or eval-interface change must be explained to parent before broad integration.

Required checks after rebasing on reviewed Plan 002:

```bash
cd python
uv sync --locked --dev --extra tracing
uv run --extra tracing pytest -q
cd ../rust
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test
cd ..
bash scripts/check-parity.sh
bash scripts/check-adapter-docs.sh
JUDGE_JEV_RUNTIME=python bash examples/run-all.sh
JUDGE_JEV_RUNTIME=rust bash examples/run-all.sh
git diff --check
```

Also run optional GEPA dependency contract tests offline, capture stress/bounds tests, installed-artifact checks and all new public CLI examples. All must pass; record actual platform limitations. No mocked quality numbers may be described as measured accuracy.

Commit logical units. Send parent exact base/head, scope list, acceptance matrix, verified results and remaining limitations. After review, push branch and create a stacked PR against `codex/audit-usability-complete` (or reconciled main if predecessors merged). Reference issues honestly; do not close manually or auto-close an incompletely delivered issue. Stop/report if unsupported external availability, credentials, paid execution/training, or incompatible schemas are required; continue independent portions while parent resolves. Two failed repair attempts require an evidence-led plan revision, not silent omission.

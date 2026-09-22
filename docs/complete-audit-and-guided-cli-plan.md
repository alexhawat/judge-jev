# Plan 002: Complete the audit corrections and make judging approachable

## Status and execution contract

- Priority P1; effort L; risk MED–HIGH (policy and validation changes).
- Planned at `c7991ad70513b987ee24f1215baadba2cf9db08d`, 22 September 2026.
- Independent development may begin at this commit. Final integration and publication depend on Plan 001, which supplies corrected replay diagnostics and Unicode projection hashes.
- Repository `https://github.com/alexhawat/judge-jev`; local Git source `/private/tmp/judge-jev-pr25-audit-20260922`.
- New worktree `/Users/alex/.codex/.chatgpt-projects/g-p-6ab22e922a588191b998828b3afa9e7c/worktrees/judge-jev-audit-complete`; branch `codex/audit-usability-complete`.
- User authorizes isolated implementation, tests, commits, push and a PR. No merging, force pushing, paid model calls, real telemetry, or installation into the user's host settings. Host installer tests must use temporary settings.
- The parent advisor maintains plan status and reviews before publication. The executor can delegate bounded implementation work to subagents, keeping ownership non-overlapping and coordinating integration. Use separate worktrees if concurrent changes would conflict.

## Intent

The engine batches a rubric's questions into one TypeSafe System One request, filters state first, routes deterministically, and emits JSON plus distinct verdict/error codes. Its current user experience requires constructing JSON, knowing environment variables and reading machine output. The user asked to implement the complete audit and make it easy to use. Deliver the engineering fixes, a guided Python user interface with preserved automation contracts, standalone assets, one real host-event integration, and honest evaluation tooling. Do not claim measured model quality without actual labeled live results.

## Current state and conventions

Read repository `AGENTS.md`, `llms.txt`, README, both manifests, and tests before changing files. Python uses dataclasses, pytest, argparse, `JudgeJevError`/`RubricError`; Rust uses serde, anyhow, integration tests and a manual CLI parser. Follow existing conventions unless a small shared helper removes duplication. Both runtimes pin `jev-1.13.0` through YAML and must agree on machine behavior. Confidence is matched-rule confidence, not probability of correctness; noul 0.5 is uncertainty; scores may be fractional between criteria levels. Missing judge answers must escalate. Mocks prove mechanics only.

Confirmed current code:

```python
# python/src/judge_jev/routing.py:116
if rule.default:
    return rule.verdict, rule.reason, "route", [], 0.0
```

This bypasses automatic verdict confidence floors. `rust/src/routing.rs` has the equivalent early return. `rust/src/funnel.rs::load_input` deserializes any JSON Value and returns it, while `state_filter.rs` returns a non-object unchanged. Python rejects non-object input but uses None as the missing-field sentinel. Both filters merge parent/child projections in an order-dependent way.

```bash
# scripts/judge-jev
cd "$ROOT"
exec uv run --directory "$ROOT/python" judge-jev "$@"
```

This loses the caller's working directory. The dispatcher prefers saved runtime over explicit environment override, builds Rust only when its binary is absent, and uses exit 1 for invalid runtime. Setup writes runtime selection before installation. The wheel currently packages only `src/judge_jev`, excluding `shared/rubrics`. Rust also locates rubrics through an external checkout.

The shipped assistant rubric's hallucination rule requires BOTH risk >=0.7 AND helpfulness confidence <0.6. Thus high helpfulness confidence can hide factual risk. Safety screens at 0.5 can reach a quality-only pass rule. Injection instructions omit `reply`; trajectory instructions need all untrusted fields checked. Existing hooks read a static `JUDGE_JEV_INPUT`, not host events. Claude PostToolUse cannot prevent completed actions. Most adapters are recipes/stubs.

## Scope and architecture boundaries

In scope: runtime source/tests/manifests, shared rubrics/schemas/fixtures, launch/setup/parity/check scripts, CI, examples, documentation, hooks/adapters, and committed copies of these plans. New modules and tests within these directories are allowed. Do not alter model pin, add dimensions for their own sake, change TypeSafe endpoint defaults, send secrets, install global packages, or edit unrelated synced workspace `sources/`.

Keep `run` and `replay` JSON-first and preserve exit codes: 0 pass, 1 fail, 2 review, 3 escalate, 4 skip, 10 no judgment due to operational failure, 11 usage failure. Add fields compatibly; older results lacking new provenance remain readable with an explicit limitation. Avoid a full rewrite. Python is the recommended human frontend; Rust remains a supported machine engine with full help. If guided commands are Python-only, public launchers must route those commands explicitly and help must clearly describe the support boundary rather than silently failing after selecting Rust.

## Workstream A: Trustworthy results and shared contracts

**Upstream reconciliation:** PR #25 merged at `689ff44` with `58bfb49` while implementation was running. Plan 001 will rebase on this new main; integrate that reviewed branch to inherit it. Its projection hash uses Python-compatible ASCII escapes, and its injection heuristic now enforces escalation. Preserve these merged semantics and explain them accurately; the advisory-only wording in original step A7 is superseded. Continue to distinguish deterministic substring heuristics from measured model safety guarantees. Tracing now uses the token's region unless explicitly overridden; preserve this behavior.

1. Add regression tests before fixes. Reject non-object JSON at Rust loading AND filtering boundaries, with zero client calls. Validate all live, mock-override and replay answers through a shared per-runtime boundary. Present answers must have correct question type, required fields, finite numbers, noul/confidence/probabilities in [0,1], scores in [0,len(criteria)-1] (fractional allowed), choice membership and valid probability-map keys. Reject booleans masquerading as numbers. Missing expected answers must never pass: preserve documented escalate behavior; a wholly empty answer response is operational error 10. Do not require mock probability sums to equal one without reconciling SDK contract and fixtures.
2. Validate rubric floors as finite [0,1], routing numeric thresholds finite and compatible with referenced fields, score/choice criteria nonempty, and malformed filter syntax at rubric load in both runtimes. Route default pass/fail through the same floor handling (zero evidence means confidence zero). Errors must be useful and consistent; invalid JSON NaN/Infinity must fail before routing/output.
3. Replace Python missing sentinel with a distinct object. Normalize redundant parent/descendant paths or track placeholders so selecting a parent includes its full value regardless of path ordering. Preserve null, heterogeneous arrays, nested lists, required-field semantics. Match Python/Rust behavior on empty strings/arrays and mock input presence. Reject unsupported malformed input consistently rather than differing on truthiness.
4. Preserve large integer exactness across runtimes (preferred: serde arbitrary_precision plus exact canonical integer handling) OR explicitly reject the same unsupported integer domain in both before any request. Document the choice. Add values around i64/u64 boundaries and beyond, Unicode, escapes, null, nested floats and key ordering to differential tests. Do not pretend all numeric serialization is identical if verified exceptions remain.
5. Enforce Rust retry total budget across both backoff and each request timeout using remaining monotonic time. Use injectable clock/sleeper/transport for deterministic tests; no real slow-network calls. Inspect Python SDK behavior and accurately document its own guarantee rather than asserting unverified parity.
6. Add `rubric_hash` (or equivalently clear field) from a canonical representation of the complete effective rubric, including ordered rules, question criteria/instructions, model, filter required flags, stakes/floors. Distinguish this from Plan 001's path-name-only projection hash. Replay rejects same-version content drift unless the explicit existing override is provided; absent hash on old results works with an explicit explanation warning. Both runtimes and schema agree. Do not overwrite the saved source provenance as if new model evidence were collected.
7. Describe deterministic checks as diagnostics versus enforcement. Make completeness/confidence enforcement consistent with routing; injection heuristic metadata is advisory and cannot be advertised as an independent model safety guarantee. Preserve Plan 001's historical replay evidence semantics.

Verify A: full Python/Rust suites and parity script; regression cases must call real routing/filtering/loaders, not copies of implementation. Add shape/field/version fixtures in existing pytest and Rust integration-test styles.

## Workstream B: Reliable installation, launchers and a guided CLI

1. Repair Bash/PowerShell launchers: environment override > saved runtime > Python default; preserve caller-relative input/replay/rubric and new file arguments (including spaces and stdin `-`); pre-runtime failures return 10 or usage 11, never a verdict code. Separate dependency preparation from judge execution so judge codes pass through unchanged. Rebuild/check Rust incrementally to avoid stale binaries. Transactional setup installs and smoke-tests before atomically persisting selection; failure preserves old configuration. Validate interactive choices and noninteractive defaults consistently. Avoid exposing or storing keys in plain text configuration.
2. Implement root and command help for Rust; expand Python help with examples/input shapes/error codes. No argument on a TTY opens a short task menu; non-TTY never prompts/hangs and returns useful usage. Handle cancellation/EOF cleanly without tracebacks. Python remains the default recommendation, not a language-choice quiz.
3. Add human commands: `init`, `doctor`, `reply`, `trajectory`, `input template`, `input validate`, `explain`, and `history` as a coherent small surface. Exact names may be simplified only with documented mapping to every capability. `reply` accepts prompt/reply/context text files and guided multiline input (a clear terminator or editor; no shell injection). Trajectory accepts a documented input file and offers a template/guided file selection. `--mock` is visibly an offline demo. Live mode states that it uses the API; never silently falls back to mock. Noninteractive required inputs produce usage errors.
4. Add explicit `--format human|json`. Existing `run/replay` default JSON; new guided commands default human. JSON mode suppresses prompts and emits exactly one JSON value on stdout, including with tracing. Human results show verdict, actual routing reason, relevant scores/scales, deciding confidence/floor, mock/live status, limitations and a concrete next step. Never invent evidence quotes or probability-of-correctness claims.
5. `init/doctor` explain selected runtime and its precedence, dependency/runtime availability, rubric location, key presence (only boolean/masked), tracing availability, offline smoke and fixes. No network by default. Input templates explain required/optional fields. Raw `run` missing optional artifacts may still reach skip; guided reply validation requires a usable prompt/reply. Avoid inconsistent hidden defaults.
6. Explain saved results offline: ordered rules, evaluated comparisons, short-circuit/unevaluated rules, deciding answers, floor and provenance, historical-only gates and rubric drift limitations. Use the same routing functions or shared evaluation trace so explanations cannot contradict decisions. Save enough provenance to distinguish original judgment from replay. Add opt-in local history storing results only by default, restrictive permissions where supported, atomic writes, list/show/replay/delete and a documented retention limit. Do not silently retain raw private input. Deletion requires an explicit command; bulk deletion must be deliberate.
7. Bundle rubric/schema assets into Python wheels and Rust distributable binary/package, with source checkout/custom root support preserved and validated. Test fresh wheel installation and Rust binary execution from unrelated temporary directories without `JUDGE_JEV_ROOT`. Input files must still resolve against caller cwd. Use packaging mechanisms rather than runtime downloads.
8. Fix pipeline example so operational failures reach fail-open/fail-closed handling before JSON summarization. No-key demonstration must reach the advertised error branch. Test a fake runner for every verdict and operational code. Update quickstart to one successful offline experience followed by clear live setup; include screenshots/text examples only if derived from actual CLI output.

Verify B: subprocess tests for both public launchers, no-argument/help/TTY/EOF/non-TTY behavior, cwd collisions/spaces, stale Rust source, setup rollback, every exit code, clean stdout, installed package execution. Use temporary HOME/config/settings and mocked subprocesses only where execution would otherwise change the host. Add actual Windows/PowerShell CI; local unavailable Windows checks are explicitly reported, not claimed executed.

## Workstream C: Policy, one complete integration, and honest measurement

1. Version both shipped rubrics for conservative safety uncertainty handling. Preserve priority for known unjudgeable/injection/harm/escalation; add review bands so noul 0.5 safety uncertainty cannot yield quality-only pass. High unsupported-fact risk must review regardless of helpfulness confidence. Define and document chosen thresholds as policy defaults requiring calibration, not empirically proven probabilities. Include `reply`/`final_output` and all appropriate untrusted fields in injection questions. Keep thresholds/questions YAML-owned. Test each boundary immediately below/at/above, happy paths and existing five-verdict examples; don't merely update goldens to make them green.
2. Primary integration defaults to Claude Code unless the user supplies another preference. Read current official host hook documentation before implementation. Build an installer with preview/dry-run, safe merge of existing settings, backup and uninstall of only owned entries. Commands use correctly quoted absolute executable paths and never assume checkout cwd. Read actual host-event stdin, capture the current user goal and per-session trajectory, isolate sessions/concurrent writes and avoid path traversal from supplied session IDs. Evaluate a meaningful completed checkpoint (e.g. Stop), not every harmless read. Translate host decisions according to current contract and prevent recursive stop loops. Retrospective evaluation must never claim to prevent actions already completed. A pre-action gate is optional; if included it needs a dedicated proposed-action rubric and correct PreToolUse decision schema, not the completed-trajectory rubric moved earlier.
3. Test event-to-normalized-input-to-result-to-host-response end to end with recorded fixtures and an offline judge; test failure policy and malformed events, concurrent sessions, recursion and installer preservation/removal. Add `doctor` integration smoke using a recorded event. No installation into the user's actual settings and no model requests. Clearly label remaining adapters as manual/experimental; remove misleading static-input automation and enforcement claims, fix Cursor decision examples using current official docs, and keep examples copyable.
4. Ship an opt-in evaluation command/tool accepting labeled JSONL cases and saved or explicitly live outputs. Include curated illustrative ordinary/failing/ambiguous/adversarial cases for each rubric, clearly marked as a smoke dataset with human-authored expectations. Report confusion matrix, false-pass count/rate with denominator, review burden, missing/error count, usage/cost when actually available and latency provenance. Separate replay/mock pipeline checks from real accuracy reports; never present canned mock quality metrics as judge efficacy. Validate datasets and support reproducible report files. Live mode is explicit, not invoked in this task. Document how to obtain reviewed labels/holdout cases and calibrate thresholds. No invented live baseline or cost values.

Verify C: policy tests, adapter contract fixtures, offline evaluator known confusion-matrix fixture, installer round-trip tests, docs checker. Include host documentation URLs and retrieval date in docs.

### Open-issue reconciliation (user follow-up)

The current open issues are #11, #15, #16 and #17, including comments read on 22 September 2026. Do not duplicate their scope or claim complete closure from partial foundations. See `003-open-issue-crosswalk.md` for the decision record.

Integrate the overlapping #11 requirements into C4: offline threshold/floor sweeps that do not edit YAML or call the model; a CI agreement-floor option that fails on regression; overall/per-verdict agreement, precision/recall/F1 with support and macro-F1; distinct actual-fail/actual-escalate predicted-pass cells; disagreements with deciding answers/confidence and confidence-floor downgrades; tokens/calls by rubric. Accept optional human score labels and report MAE, normalized MAE and fit; define display rounding as clamped `floor(score + 0.5)`, without permitting out-of-domain raw model scores. Preserve train/held-out/frozen-red-team split labels and gate red-team regressions independently of aggregate improvement. Add a distribution report for confidence mean/median, floor/downgrade counts and rule frequencies/margins where provenance supports them. Missing data is unavailable, never inferred as measured. Tests use known expected metrics and prove sweeps make zero API calls. Curated synthetic smoke cases do not fulfill the real-judgment corpus requirement: link #11 as related unless its full acceptance can actually be demonstrated.

#15 production capture, #16 backend abstraction/Cloudflare and #17 GEPA/GRPO-style optimization remain separate feature scope. Local history is not a nonblocking sampled production capture system. A threshold sweep is not an optimizer. Do not introduce these larger systems opportunistically into this already broad audit PR.

## Integration, verification and publication

User follow-up on 22 September 2026: update MergeCraft in CI. Upstream has no published GitHub releases; its current verified action manifest is `a591fbc15bfd05656a93442f988c42a684a2411e`, adopted by merged [upstream PR 816](https://github.com/alexhawat/mergeCraft/pull/816). That manifest pins image `sha256:b88f3583005f1412dddd4ad963b90f879300314882066320ee376d12f85aeae6`, built from source `d04e6b5d10a21b3efc1c2947ac0000951d64a8b6`. Update both `uses:` and `MERGECRAFT_ACTION_SHA` in `.github/workflows/mergecraft.yml`, refresh its stale pin comment, and preserve the remaining workflow configuration. Validate the existing `mergecraft-pin` CI guard, YAML parsing and diff whitespace. This belongs in the audit implementation branch and PR.

Commit logical units with conventional messages. Coordinate Plan 001 before changing overlapping funnels/gates: independent work can proceed, then rebase this branch onto `codex/pr25-replay-hash-fixes` once reviewed, resolving overlaps and running all tests. Keep stacked ancestry so PR 002 targets the focused branch and shows only audit improvements; if predecessors merge, reconcile to current main without overwriting others.

Required commands (add new feature/packaging/launcher tests to these suites or documented scripts):

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

Expected: every command passes; optional real-SDK tracing test executes offline rather than skips. Use `$HOME/.cargo/bin` on PATH if necessary. Network installs may need tool escalation, but no secrets should be printed. New CI covers installed artifacts, public launchers, Windows PowerShell and host fixtures. Run package isolation checks and guided CLI manual/PTY smoke, document exact commands/results and limitations. Review full diff and all new tests, not just aggregate counts.

Done means every audit row 01–12, all seven further defects, four policy risks and four product directions are mapped in a committed completion matrix to code/tests or a specific external validation limitation. External live calibration and live-host behavior remain honestly unmeasured; their tooling and fixture contracts must be complete. There must be no silently omitted implementation requirement. Include the plan and completion matrix in the PR.

Before publication send parent base/head SHAs, full changed-file list, completed matrix, verification commands/results and deviations. Parent reviews; then push and create the authorized PR with concrete behavior, dependencies and testing. Do not merge. If new blockers require changing the model contract, exposing secrets, material paid activity, dropping a requested capability or two failed repair attempts, report with evidence and continue independent work while the parent resolves the plan. Future changes to schemas, rubrics, command grammar and adapters must keep parity/packaging/host fixtures in sync.

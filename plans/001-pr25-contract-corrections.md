# Plan 001: Correct replay diagnostics and projection hashing

## Status

- Priority: P1; effort M; risk LOW–MED; category correctness/tests.
- Planned at `c7991ad70513b987ee24f1215baadba2cf9db08d` (PR #25 head at planning time).
- Reconciled onto `689ff44f2a37f00d449638d383de6b42576243f8` after PR #25 merged.
- Worktree: `worktrees/judge-jev-pr25-fixes`.
- Branch: `codex/pr25-replay-hash-fixes`. Open a focused PR against PR #25's branch unless it has merged, in which case use main after reconciling.

## Intent and current state

The user authorized implementation, tests, a new worktree/branch, push and PR. The executor edits and commits in its own worktree. Do not merge or modify PR #25's branch.

Both Python/uv and Rust/cargo expose the same machine-result contract. Use conventional commits and existing pytest/Rust integration test patterns.

At `python/src/judge_jev/funnel.py`, saved deterministic gates are reconstructed, and gates are recomputed only when absent. Rust has the same saved-gates split. A replay under a higher confidence floor can return review while preserving a stale gate that says the previous lower floor passed.

Python hashes projection paths with ASCII escapes while Rust serializes Unicode literally. A valid path such as `résumé` therefore hashes differently. The hash describes the sorted path allowlist, not content or the complete rubric.

PR #25 commit `c7991ad` already removed unsupported Logfire configuration, set console export off, fixed its README directory example, added an active tracing test, and added an optional-extra CI check. Verify this implementation rather than redoing it.

## Scope

Only edit Python/Rust funnel, gate/model/routing modules if necessary, corresponding Python/Rust tests, the parity script, judgment-contract docs/README, and this plan. Tracing module/test/CI edits only if verification uncovers a remaining defect. Do not change policy thresholds, state filtering, setup, dispatchers, host adapters, or unrelated audit findings. Do not export telemetry, use real API keys, merge, or force-push.

## Steps

1. Check the clean source and create the new worktree and branch at the planned head.
2. Run baseline tests and the tracing smoke with the optional extra.
3. During replay always recompute `answer_completeness` and `confidence_floor` from current answers, routing, and floor. For `state_projection`, `token_budget`, and `injection_heuristic`, preserve original evidence if available with a reason explicitly identifying it as historical; otherwise report skip because replay lacks raw state. Maintain deterministic ordering and identical Python/Rust output.
4. Use identical compact JSON serialization for sorted path names in Python and Rust, preserving the merged ASCII-escape contract. Cover non-ASCII, supplementary Unicode, DEL/control escaping, empty, and reordered path lists at the normalization boundary. Document the allowlist-only scope.
5. Add replay tests for stricter rubric version with drift override, changed answers, old saved results, stable field order, original versus current checks, Unicode parity, and replay diagnostics.
6. Commit the implementation and report tests and diff for review before publication. After approval, push and open a focused PR against `main`. Do not merge.

## Done criteria

- Python tests, including active tracing, pass with the locked tracing extra.
- Rust formatting, clippy, and tests pass.
- `scripts/check-parity.sh` passes, including Unicode and replay cases.
- Both runtime example suites pass.
- `git diff --check` is clean and only allowed paths changed.
- No network model calls or real telemetry exports occur.

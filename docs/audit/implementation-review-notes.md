# Early implementation review notes (not final review)

Worktree inspected while Plan 002 lead was paused for agent scheduling; scripts were explicitly unfinished/untested. Resolve before final verification:

- Bash setup still propagates native uv/cargo/smoke/config-write failures via `set -e`, potentially returning a verdict code. Map bootstrap errors to 10 and preserve old selection.
- PowerShell setup still calls Read-Host in noninteractive contexts, its ValidateSet parameter check can exit 1 before controlled error mapping, and its smoke binary paths are Windows-only. Make behavior portable or document/enforce a clear supported PowerShell version/platform.
- PowerShell launcher uses `$IsWindows`, absent in Windows PowerShell 5.1; use a portable OS check or explicit PowerShell 7 requirement.
- No-argument TTY launch with saved Rust should still reach the Python guided frontend. Current dispatch selects Python only for named guided commands.
- Existing Python venv should not silently retain incompatible dependencies after the checkout lockfile changes; add sync/freshness handling without losing caller cwd or stdout cleanliness.
- Cover missing/broken executable and setup failure paths with fake tools in temporary copied repositories; prove errors do not become judged fail/review.
- Confirm quoted numeric YAML floors/thresholds have the same acceptance policy across runtimes: Python currently float-coerces strings, while Rust's typed floor deserializer rejects strings. Prefer actual finite numbers in both. Do not unnecessarily prohibit finite comparison thresholds outside an answer domain if they intentionally make a custom rule unreachable; distinguish invalid answer values from valid policy comparisons and preserve documented compatibility.

Parent separately verified current Claude docs support exec-form `command` + `args`. Sent hook worker two corrections: reject absent/malformed/mismatched result JSON even with exit 0 under fail-closed policy, and give backups unique/exclusive names instead of second-resolution overwrite. Stateless per-event transcript normalization is a reasonable alternative to maintaining a duplicate session store.

## Parent review at audit checkpoint 35d629b

Independently passed: Python 259 tests (one local PowerShell skip), Rust 47 unit / 32 integration / 4 policy / 2 standalone tests, Rust format and clippy, full cross-runtime parity harness, adapter documentation check, both runtime example suites. Human reply demo is quiet and explicitly labeled canned. Prior evaluator missing-results/red-team false success and history retention traversal were reproduced, corrected, and regression tested.

Remaining review corrections sent to executor:

- Make history permission handling portable where `os.fchmod` is unavailable; run history round-trip in Windows CI. Consistently reject a symlink history root when loading entries.
- Complete existing-venv dependency freshness handling after manifest/lock changes; cover it with a public-launcher regression test.
- Map Bash setup directory-creation errors and interactive EOF deliberately rather than leaking exit 1.
- Resolve guided dispatch when global verbosity flags precede the command and Rust is selected.
- Reconcile completion documentation with separately merged PRs #26 and #27; final audit targets current main.

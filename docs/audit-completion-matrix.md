# Audit acceptance matrix

Source: the [original audit](audit/original-audit.md) and
[complete implementation plan](complete-audit-and-guided-cli-plan.md). This maps
every accepted finding to the shipped implementation and its verification. The
branch is based on merged main `196fedd` and includes the final review corrections
after checkpoint `35d629b`.

## Engineering findings

| Original finding | Implementation evidence | Verification / remaining check |
|---|---|---|
| 01 Non-object input bypass | Rust `funnel.rs::load_input` and `state_filter.rs` reject non-object input | Filter unit tests, input-shape tests, and malformed public CLI parity |
| 02 Invalid replay answers | Python/Rust `answers` modules validate live, mock and replay answers before routing | Answer validation and replay tests; invalid confidence and domains fail without verdict |
| 03 Default verdict floors | Python/Rust routing applies floors to default pass/fail with zero confidence | Routing tests for default automatic verdicts |
| 04 Launcher error codes | Bash/PowerShell launchers distinguish bootstrap/usage failures from verdict codes | Launcher suites cover config/native failures; Windows behavior is exercised by CI because PowerShell is unavailable locally |
| 05 Pipeline error handling | `examples/03-gate-a-pipeline/run.sh` branches before summary parsing | `scripts/check-pipeline-example.sh`; both runtime examples passed at checkpoint |
| 06 Static hooks / enforcement claims | Real Claude Stop-event adapter and installer; removed static Claude/Cursor templates | Recorded fixtures, structural result validation, installer tests, and retrospective limitation documentation |
| 07 Caller working directory | Public launchers preserve caller cwd and argument paths | Spaced-cwd/stdin/relative-file launcher tests |
| 08 Rust help | Root and per-command help in Rust CLI; Python guided help | Rust integration tests and Python format/help/cancellation cases |
| 09 Null/order filtering | Missing sentinel and order-independent parent/descendant selection in both runtimes | Python/Rust filter regressions |
| 10 Numeric validation | Typed finite floors/thresholds and answer domains in both runtimes | Answer/rubric tests; finite unreachable comparison thresholds retained intentionally; nonstandard and overflowing JSON numbers rejected before filtering |
| 11 Transactional setup | Install/smoke before atomic preference replacement | Rollback, directory-write, EOF, native-launch and PowerShell argument/error tests |
| 12 Public entry point CI | Public launcher, packaging, hook fixtures, pipeline and Windows jobs | Local suites pass; actual Windows CI required after publication |

## Seven further defects

| Finding | Implementation evidence | Verification / remaining check |
|---|---|---|
| Runtime override precedence | Explicit environment > saved preference > Python default | Launcher precedence tests |
| Stale Rust build | Public launchers invoke incremental build | Fake-tool rebuild regression and real runtime examples |
| Standalone assets | Wheel rubric/schema data and Rust embedded assets with explicit-root precedence | Isolated wheel and copied binary tests, `cargo package --offline`, and copied-asset consistency guard |
| Large integer precision | Rust arbitrary-precision JSON and exact canonical integer rendering | Boundary/beyond-u64 unit vectors and differential wire matrix with exact integers, Unicode/escapes, null and nested floats |
| Empty/malformed mock presence | Typed shipped-input validation plus matching empty artifact semantics | Empty reply and malformed field tests in both runtimes; public parity harness covers operational refusal |
| Late Rust filter validation | Parse filter paths during rubric load | Malformed rubric/filter tests |
| Retry deadline | Rust monotonic remaining budget caps per-attempt timeouts and sleeps | Deterministic retry tests and local HTTP retry parity; do not claim an unmeasured whole-SDK wall-clock guarantee |

Python dependency freshness after lockfile changes was an additional review correction to launcher reliability. Final launcher tests must prove an existing executable does not suppress dependency synchronization.

## Policy risks

| Risk | Implementation evidence | Limits |
|---|---|---|
| Uncertain safety can pass | Versioned YAML review bands and exact boundary tests in both runtimes | Thresholds are policy defaults, not empirical calibration |
| Factual risk hidden by quality confidence | Independent unsupported-fact review rule | No real-world factual accuracy measurement performed |
| Injection omits evaluated artifact | Instructions include reply/final output and relevant untrusted fields | Model robustness unmeasured; inherited deterministic heuristic is explicitly distinguished |
| Confidence overstates quality | Human output/docs distinguish deciding-answer confidence, heuristic override and canned evidence | Not a probability that the whole artifact is safe/correct |

## Product directions

| Direction | Delivered surface | Verification / remaining check |
|---|---|---|
| A Guided starting point | No-argument TTY menu; live/demo choice; init/doctor; Python recommendation; no non-TTY prompts | Real PTY menu and multiline demo verified; doctor identifies the runtime exercised and reports the recorded integration smoke |
| B Simple inputs/readable results | Reply text/files/editor, trajectory/template/validate, human/JSON formats and next steps | Guided tests, hands-on demo, editor stdout isolation and portable argv, cancellation, and Rust human-format tests |
| C One complete integration | Claude Code Stop normalization, safe install/backup/uninstall, loop guard, host response and failure policy | Recorded offline fixtures only; no actual user settings installation or live-host run; every other adapter is labeled manual/experimental |
| D Explain/history/evaluation | Shared rerouting trace, complete rubric hash/source provenance, opt-in result-only history, offline evaluator/sweeps | History ownership and portable permissions, explanation values/provenance/historical gates, and fresh-result distribution metrics are regression-tested |

## Issue and PR reconciliation

- PR #25 merged upstream; its tracing/ASCII projection hash/enforced injection semantics are preserved.
- Focused replay/projection corrections shipped and merged in PR #26.
- MergeCraft update shipped separately and merged in PR #27; it is not an audit PR change.
- Issue #11: evaluator tooling overlaps this PR, but the synthetic smoke corpus does not fulfill the real labeled-data requirement. Use a related reference, not an automatic closing keyword.
- Issues #15/#16/#17 are implemented in the separate capture/backends/tuning branch; they must not be claimed as delivered by the audit PR.

## Verification

At checkpoint `35d629b`: 259 Python tests passed, one local PowerShell test
skipped; Rust 47 unit + 32 integration + 4 policy + 2 standalone tests passed;
Rust fmt/clippy, full runtime parity, both runtime example suites and adapter-doc
checks passed. After final review corrections, the local suites report 280 Python
tests passed with two PowerShell-only tests skipped, and Rust 47 unit + 34
integration + 4 policy + 3 standalone tests passed. Rust fmt/clippy, package asset
checks, pipeline tests, adapter documentation checks, both example suites, and the
expanded runtime parity harness pass. Windows CI remains the source of actual
PowerShell evidence; it was not available locally.

No live model calls, billed calibration, telemetry export, real host settings mutation or local model training were performed. The implementation and offline contracts are complete; these external validation limits remain explicit.

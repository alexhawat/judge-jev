# judge-jev: repository and usability audit

Audited 22 September 2026 against [`21c71f7eff4929fcfe5f927986e1adc3dee8de9c`](https://github.com/alexhawat/judge-jev/tree/21c71f7eff4929fcfe5f927986e1adc3dee8de9c). This is an assessment and proposed product direction, not an implemented change or a set of approved implementation plans.

**Follow-up:** PR #25 at `e2cc7df` was reviewed separately. It adds contract metadata and optional tracing; it does not resolve the main correctness or usability findings below. See [PR #25: effect on priorities](pr25-plan-update.md) for verified gaps and the revised sequence. PR #25 was open and unmerged when checked.

**Assessment:** judge-jev has a useful evaluation engine and substantial engineering tests, but its everyday workflow is unfinished. It is usable by a developer comfortable with JSON, environment variables, and shell exit codes. It is not yet a guided tool that a person can install and immediately understand. The safety-hook descriptions overstate what the supplied integrations do.

## What it does

You supply an artifact and a rubric. The program filters the input to the rubric's selected fields, builds eight questions for either built-in rubric, makes one TypeSafe System One request using the configured Jev model, and applies ordered rules to the answers. It returns a structured result and an exit code.

| Rubric | Input | Questions concern |
|---|---|---|
| `assistant-reply` | `prompt`, `reply`, optional `context` | Judgeability, injection, intent, unsupported factual claims, harmfulness, helpfulness, coherence, human review |
| `agent-trajectory` | `goal`, `steps`, `final_output` | Judgeability, injection, task type, unauthorized writes, looping, goal alignment, efficiency, human review |

The five labels are `pass`, `fail`, `review`, `escalate`, and `skip`. The CLI itself does not create a human-review queue, contact anyone, prevent a tool operation, repair an answer, or verify external facts. A caller must implement those actions. The example scripts describing publishing or paging primarily print messages illustrating the intended integration.

`replay` recalculates routing from saved answers without calling the model. It can help test threshold changes, subject to the version-drift check. It does not reconsider the underlying text or produce fresh evidence.

`--mock` exercises the program offline. Its responses largely depend on question names and canned values, with a small input-presence/injection heuristic. A mock pass is not evidence that an answer is correct, useful, or safe.

The “screen → profile → locate → score → route” funnel describes question organization and rule precedence. All questions are sent together; a screen result does not save the cost of asking the remaining questions. Shared YAML is a good policy boundary, and keeping numerical routing in code is a sound design choice.

Evidence: [Python funnel](https://github.com/alexhawat/judge-jev/blob/21c71f7eff4929fcfe5f927986e1adc3dee8de9c/python/src/judge_jev/funnel.py#L72), [mock engine](https://github.com/alexhawat/judge-jev/blob/21c71f7eff4929fcfe5f927986e1adc3dee8de9c/python/src/judge_jev/typesafe_client.py#L79), [shared rubrics](https://github.com/alexhawat/judge-jev/tree/21c71f7eff4929fcfe5f927986e1adc3dee8de9c/shared/rubrics).

## How good is the CLI? Is it interactive?

**Setup is minimally interactive; judging is not interactive.** Setup asks Python or Rust. There is no guided judgment form, multiline answer editor, input-template generator, results browser, or review workflow. Running the Python CLI with no arguments returns usage error 11. Its help works, but supplies little explanation of expected input. Rust rejects `--help` as an unknown command, and `run --help` as an unknown flag.

| Area | Assessment |
|---|---|
| Automation primitives | Useful: stdin, JSON output, separate stderr logs, distinct verdict/error codes, replay |
| First-run experience | Weak: asks about implementation language before the user's task; requires a toolchain and manual key configuration |
| Input preparation | Weak: users build rubric-shaped JSON themselves; `rubric show` lists questions/rules but omits the input template and field requirements |
| Human-readable output | Weak: primary CLI prints full JSON; a readable summary exists only in example helpers |
| Discoverability | Uneven: Python help exists; Rust help is missing; no guided next step |
| Installation | Checkout-oriented: wrappers run inside the repository; Python wheel includes source package but not shared rubric assets |
| Integrations | Primarily manual recipes; OpenClaw explicitly says “stub integration”; Hermes requires the user to implement a placeholder |
| Evaluation validity | Undemonstrated by the repository's tests: mocks and recorded responses verify mechanics, not real judge accuracy |

Two runtimes currently multiply parsing, filtering, installation, timeout, and packaging edge cases. They do not give beginners an obvious benefit. Make Python the recommended first-run path and keep Rust as an advanced option until there is a measured deployment requirement for both.

## Prioritized engineering findings

Effort: **S** = hours, **M** = roughly a day or a few days, **L** = several days or more, including tests. Risk is the risk of the proposed fix, not the severity of the defect. These are estimates, not delivery promises. All rows below are high-confidence source findings; reproduction details distinguish executed checks from source-only analysis.

| ID | Finding / category | Impact | Effort | Fix risk | Evidence |
|---|---|---|---|---|---|
| 01 | Rust bypasses input filtering for non-object JSON — correctness/privacy | Entire arrays or scalars can reach the live API despite field restrictions; required paths are bypassed | S | Low | `rust/src/funnel.rs:53`, `rust/src/state_filter.rs:128` |
| 02 | Python replay accepts invalid deciding-answer confidence — correctness | A malformed result can return pass with missing confidence or NaN confidence | M | Medium | `python/src/judge_jev/funnel.py:162`, `python/src/judge_jev/routing.py:54` |
| 03 | Default automatic verdicts bypass confidence floors — correctness | Custom rubrics can emit pass/fail at confidence 0 | S | Low | `python/src/judge_jev/routing.py:116`, `rust/src/routing.rs:79` |
| 04 | Public launchers violate the error-code contract — CLI reliability | Invalid runtime returns 1, which also means a genuine fail verdict | S | Low | `scripts/judge-jev:27`, `scripts/judge-jev.ps1:63` |
| 05 | Pipeline example crashes before its error policy — integration | API/setup errors bypass the demonstrated handling branch | S | Low | `examples/03-gate-a-pipeline/run.sh:24`, `examples/_summary.py:10` |
| 06 | Hooks judge static input and misdescribe enforcement — integration | Repeated calls can evaluate old data; post-tool checking cannot prevent the completed action | M–L | Medium | `hooks/claude-code/settings-snippet.json:17`, `:23`; `hooks/cursor/post-tool-use.json:3` |
| 07 | Launchers lose the caller's working directory — CLI correctness | Relative paths from another project fail or select a same-named file in the judge checkout | S | Medium | `scripts/judge-jev:14`, `:18`; `scripts/judge-jev.ps1:33` |
| 08 | Rust has no help command — CLI usability/parity | The most natural discovery command exits 11 | S | Low | `rust/src/main.rs:44`, `:118`, `:173` |
| 09 | State filtering loses nulls and is order-dependent — correctness/parity | The two runtimes can judge different input; broader selections can still omit original values | M | Medium | `python/src/judge_jev/state_filter.py:119`, `:138`, `:150`; `rust/src/state_filter.rs:101` |
| 10 | Numeric validation is incomplete — correctness | Out-of-range confidence/floors and invalid answer domains can bypass intended checks or violate output schema | M | Medium | `python/src/judge_jev/rubric.py:198`, `rust/src/rubric.rs:51`, `rust/src/models.rs:118` |
| 11 | Setup writes selection before successful installation — onboarding | A failed switch leaves the launcher pointed at an unusable runtime | S | Low | `scripts/setup.sh:19`, `scripts/setup.ps1:32` |
| 12 | Public entry points and host contracts lack CI coverage — tests | Green CI misses the launcher and hook failures above; Windows scripts are not exercised | M | Low | `.github/workflows/ci.yml:9`, `:72`; `scripts/check-parity.sh:26` |

### 01. Reject non-object inputs before filtering

Rust parses any JSON value and the filter immediately returns a non-object unchanged. Python rejects a non-object in `load_input`. For example, a top-level array containing fields excluded by the rubric would still be transmitted by Rust in live mode. This is a concrete failure of data filtering, not a claim that the public API is an attacker-controlled endpoint. Verified by tracing the complete Rust load/filter/request path; no live transmission was performed.

Require a top-level object in both runtimes, and enforce this again at the filter boundary. Add array/string/number/bool/null fixtures and assert zero network requests for all invalid inputs.

### 02. Validate answers before replay or routing

Python checks only that replay input has `rubric_id` and `answers`, then hands its contents to routing. `decision_confidence` drops answers whose confidence cannot be found. I executed replay using the shipped assistant rubric with both deciding scores at 3, one deciding confidence omitted, and the other at 0.9: **pass, confidence 0.9**. Replacing the missing value with NaN produced **pass, confidence NaN**. Python's JSON parser accepts NaN by default, and NaN is not less than the floor.

Use a common validation boundary for live answers, mock overrides, and replay. Check the expected question type, required fields, finite numerical values, confidence/noul bounds, and score/choice domains. Older saved results need an explicit compatibility policy; silently ignoring missing decision evidence is unsuitable.

### 03. Gate default pass/fail rules too

Both routers return from a default rule before checking the confidence floor. A custom default pass at destructive stakes returns **pass with confidence 0**, reproduced in Python. Both shipped rubrics default to review, so this is a customization bug rather than the normal shipped path. Either prohibit automatic default verdicts or send them through the shared floor logic.

### 04. Preserve the error contract at the public launcher

`JUDGE_JEV_RUNTIME=not-a-runtime bash scripts/judge-jev --help` returned exit **1** in the clean audit checkout, with “Unknown runtime …”. That means “fail” to a consumer trusting the advertised contract, even though no judgment occurred. The PowerShell unknown-runtime branch also exits 1; subprocess/bootstrap failures can similarly escape with their native statuses.

Translate failures before the runtime begins judging into a consistent operational code. Forward verdicts unchanged only once the judge actually ran. Test wrappers, not just runtime entry points.

### 05. Repair the copyable gate example

The pipeline example calls `ex_summary` before inspecting the saved exit status. An operational failure produces no result JSON, so the summary parser throws and `set -e` exits before the fail-open/fail-closed case. Reproduced with a deliberately invalid runtime, without calling the model. The suggested no-key demo also exits in `_common.sh` before it reaches the advertised branch.

Inspect the exit code before parsing output. Only format a result when there is a verdict. Test every operational and verdict branch through a deterministic fake runner. This is high leverage because the adapter documentation tells users to copy this example.

### 06. Capture real hook events and state what enforcement means

Both hook commands ignore host-event stdin and pipe the environment variable `JUDGE_JEV_INPUT` into the judge. The Claude README tells users to set that JSON before a tool-heavy task. There is no event normalization or trajectory accumulation that makes it reflect each subsequent tool operation. Unchanged input can therefore be rejudged repeatedly, incurring cost without inspecting current activity.

The Claude adapter advertises blocking escalation but registers `PostToolUse`, after the action has completed. Such feedback cannot prevent the completed file write or command. Cursor maps escalation to exit 1, which its documented default contract treats as a failed hook that allows the action to proceed. These claims were checked against [Claude's hook reference](https://code.claude.com/docs/en/hooks#posttooluse-decision-control) and [Cursor's hook reference](https://prod.cursor.com/docs/hooks#command-based-hooks).

First separate retrospective audit from pre-action authorization in names and documentation. Then build one real adapter that reads host events, maintains session-specific context, and returns the host's expected decision shape. A pre-action gate needs a rubric for proposed actions; moving the current completed-trajectory rubric to an earlier event is insufficient.

### 07. Resolve file paths relative to the caller

The shell and PowerShell dispatchers change directory to the judge repository before interpreting `--input`. Python's `uv --directory` then uses the Python project directory. The runtime fallback can locate repo fixtures, but it cannot recover the original caller's directory. If a user invokes the launcher from their own project with `--input reply.json`, the intended file is not searched there.

Preserve the calling directory or resolve file-valued arguments before dispatch, retaining `-` as stdin. Test different directories, spaces, same-named files in both projects, and replay paths. Until fixed, use absolute paths or stdin.

### 08. Implement help consistently

Python `--help` and `run --help` were exercised and exited 0. Rust only special-cases `--version`; its parser treats help as an invalid command/flag. Add root and per-command help with expected input, runnable examples, error codes, and clear next steps. Automated parity tests currently check version and malformed arguments but miss this basic user action.

### 09. Correct filter semantics

Python uses `None` as its missing sentinel. Selecting `{"a": null}` therefore yields `{}`; making `a` required rejects it. Rust distinguishes absence from JSON null and preserves it.

Both implementations retain the existing value when overlapping projections have incompatible types. Executed example: input `{"a":[1,{"x":2},null]}` with paths `a[].x` then `a` produces `{"a":[{},{"x":2},{}]}`. Reversing the paths preserves the original list. Selecting a parent should include that parent's complete value regardless of ordering. Use a missing sentinel and normalize redundant parent/child selections or track projection placeholders explicitly.

### 10. Validate numerical policy and answer domains

Rubric loaders verify that a confidence-floor entry exists, but do not enforce finite values between 0 and 1. Python coerces values with `float`; Rust uses unrestricted `f64`. A negative floor defeats the intended protection, and NaN makes the downgrade comparison false. Rust answer structures also accept numerical confidences outside the declared result range. Score ranges and choice membership need checking against the rubric, not merely deserialization into a number/string.

Validate all policy and answer boundaries before applying rules. The shared JSON schemas are useful contracts, but their existence does not mean every runtime input is validated against them.

### 11. Make runtime setup transactional

The shell and PowerShell scripts write `.judge-jev/runtime` before checking tool availability or install success. Preserve the prior working selection until the new runtime has installed and passed a smoke check. Validate input rather than treating arbitrary menu answers as a runtime choice. The Python internal setup treats any non-`1` answer as Rust, while shell/Rust setup treats any non-`2` answer as Python; this should be made consistent too.

### 12. Test what users actually run

The repository has valuable unit tests and direct cross-runtime parity checks. However, those checks invoke runtimes directly, bypassing dispatchers. Adapter CI checks README wording about mock mode, not host payloads or behavior. The example matrix exercises its expected mock verdicts rather than its error branches. All configured runners are Ubuntu.

Add public-launcher contract tests, hook payload/decision fixtures, setup failure rollback, Windows/PowerShell execution, installation outside a checkout, and golden help/input/result examples. Add differential cases for null, non-object JSON, empty strings, large numbers, missing answer fields, and default automatic verdicts.

## Further defects and limitations

These are worth tracking after the primary contract and usability fixes.

| Finding | Evidence and impact | Effort / risk / confidence |
|---|---|---|
| Runtime override examples are misleading after setup | `scripts/judge-jev:8–11` prioritizes the saved runtime over the environment. The README's “try Rust” environment example cannot switch a configured Python checkout. This precedence is documented elsewhere, so the defect is the contradictory example/UX. | S / Low / High |
| Rust launcher can execute an old build | `scripts/judge-jev:21–25` builds only when the binary is absent. After pulling source changes it can run the existing stale executable. Parity script `:18–24` explicitly fixes this for its own tests, leaving public dispatch unchanged. | S–M / Low / High |
| Standalone packages lack rubric assets | `python/pyproject.toml:26–27` packages only `src/judge_jev`; `paths.py:36–37` expects repository assets. Rust `paths.rs:26–47` also depends on an external checkout. Package installation alone is not a complete out-of-checkout product. This is a distribution limitation, not a tested published-package failure. | M / Medium / High |
| Rust large integers lose precision | `rust/src/canonical.rs:39–42` acknowledges integers beyond its integer domain become floating-point values; Python preserves them. An input such as `18446744073709551617` cannot satisfy the universal byte-identity promise. Preserve exact values or reject a shared unsupported domain. | M / Medium / High |
| Rust mock presence semantics differ | `rust/src/typesafe.rs:274–284` treats empty-string reply as present; Python `typesafe_client.py:101` does not. Mock pass/skip behavior can differ on empty/malformed inputs. | S / Low / High |
| Rust validates filter syntax too late | `rust/src/rubric.rs:48–61` never parses filter paths; `state_filter.rs:148` does it at run time. `rubric show` or replay can accept malformed paths that Python rejects at load. | S / Low / High |
| Retry budget is not a strict wall-clock deadline | `rust/src/retry.rs:140–166` checks the total budget before sleeping but gives a subsequent request its full timeout. A retry starting near second 29 can run beyond the advertised 30 seconds. Source-confirmed deadline weakness; no slow-network timing experiment was run and SDK parity was not claimed. | S–M / Low / High |

## Policy risks: separate from implementation bugs

**A confident pass is currently much narrower than “safe and correct.”** The matched-rule confidence definition is intentional and documented, but the rules can still surprise users.

1. **Uncertain safety screens can pass.** I ran the shipped assistant rules with judgeability, injection, harmfulness, and escalation all at 0.5, and helpfulness/coherence at 3 with confidence 0.9. Result: pass with confidence 0.9. The pass rule reads only the two quality scores. Add explicit uncertainty-review rules or require sufficiently certain negative safety signals for automatic pass. Effort M, fix risk Medium, behavior confidence High; threshold choices require calibration. Evidence: `shared/rubrics/assistant-reply.yaml:95–128`.
2. **Unsupported facts can pass.** Hallucination risk 1.0 with otherwise healthy, confident quality scores returns pass 0.9. The hallucination review rule additionally requires low helpfulness confidence, which is a different concept. Define factual support separately from helpfulness, especially when context is absent. Effort M, risk Medium, confidence High. Evidence: the same rubric `:118–128`.
3. **The injection question omits part of the artifact.** Assistant screening explicitly names prompt/context but excludes reply; trajectory screening names goal/observations but excludes final_output. The mock scans the whole serialized input and can mask this discrepancy. Broaden the live screening instructions and test adversarial artifact text and benign quotations. Effort S–M, risk Low, confidence High about the omission; live exploitability remains untested. Evidence: both rubrics `:32`, Python mock `typesafe_client.py:100–103`.

The current “confidence” should be presented as confidence in the deciding answers, not a calibrated probability that the entire artifact is correct. There is no labeled real-world benchmark in the inspected test suite establishing false-pass rate, false-review rate, or adversarial robustness. A recorded API-shape fixture is valuable interoperability evidence, but cannot establish quality by itself.

## Make it easy: proposed product direction

These are proposed commands and behavior, not features available today.

### A. One guided starting point

Run `judge-jev` in an interactive terminal and show a small menu: judge a reply, judge an agent run, open a previous result, or check setup. Noninteractive invocations must never hang on a prompt. Default installation should choose Python; runtime selection belongs in advanced settings.

`judge-jev init` should locate dependencies, explain where to configure a TypeSafe key without echoing it, offer a clearly labeled offline demonstration, and finish with an actual ready-to-use command. `judge-jev doctor` should name the selected runtime and why, rubric locations, key presence, and exact corrective steps. A paid connectivity check should be an explicit action.

Grounding: current setup only chooses a runtime; current no-argument CLI is a usage error. Estimated effort M. Trade-off: interactive behavior adds tests and terminal concerns, so it should wrap the existing run service rather than duplicate evaluation logic.

### B. Simple input and readable results

Proposed invocation: `judge-jev reply --prompt-file question.txt --reply-file answer.txt --context-file sources.txt`. In interactive mode, permit multiline editing or file selection. Keep `run --rubric ... --input ...` for existing scripts. Provide an input-template command and local input validation before any billed request.

A terminal result should show verdict, plain-language reason, failing or uncertain checks, score scale, deciding-answer confidence, mock/live status, and the next action. An example for a genuinely uncertain result could read:

```text
REVIEW — Check the factual claims before publishing

Helpfulness           3.2 / 4
Coherence             3.6 / 4
Factual support       Needs review
Decision confidence   0.58 (required: 0.70)
Mode                  Live

Next: add supporting context or ask a person to verify the claims.
```

This illustrates a future result layout, not an observed Jev output. Preserve existing machine output by adding explicit `--format human|json`; a new interactive command can default to human output without breaking existing `run` consumers. A `--json` path must be stable and emit no prompts or progress text on stdout.

Grounding: the core already has scores, reasons, deciding answers, usage and mock flags; the example summary already extracts some. Estimated effort M–L. Trade-off: richer explanations must distinguish actual evidence from inferred advice; do not invent supporting quotations the model did not return.

### C. Ship one complete integration

Choose a primary host and provide an integration installer, event normalization, isolated session context, correct decision translation, and a doctor check that exercises a recorded event. Preserve existing host settings and show the exact changes. Label other adapters as manual recipes until tested to the same standard.

Grounding: current static-input hook commands and explicit adapter stubs. Estimated effort L. Trade-off: host APIs change, so support one reliable path before spreading maintenance across seven hosts. Prefer meaningful checkpoints over a billed evaluation after every harmless read operation.

### D. Explain and calibrate decisions

Proposed `judge-jev explain result.json` should show which rules were checked, why they did or did not match, the selected floor, and limitations. Save a rubric content hash and relevant provenance to make replay robust even if someone edits YAML without bumping its version. Add an optional local history with clear retention and deletion controls, since inputs can contain private information.

Before marketing the tool as a safety gate, evaluate a labeled set of ordinary, failing, ambiguous, and adversarial cases with actual model calls. Measure false passes, review burden, cost and latency per rubric. This should inform thresholds and uncertainty handling.

Grounding: replay and result provenance already exist, but reasons are static routing text and quality tests use mocks/recordings. Estimated effort L. Trade-off: a meaningful dataset and expert labels cost more than another UI feature, but they are necessary to assess the judge's actual usefulness.

## Recommended sequence

1. Add regression cases for the confirmed contract failures; fix non-object filtering, replay validation, confidence defaults, and wrapper error mapping.
2. Repair the copyable pipeline example and accurately label existing hook behavior.
3. Fix help, relative paths, transactional setup, and runtime selection clarity. These directly reduce daily friction.
4. Deliver the guided input flow and readable results on the recommended Python path, maintaining existing automation contracts.
5. Build one tested host integration; then calibrate policy and improve explanation/history.

Characterization tests must accompany policy/filter changes before broader refactoring. Do not expand the adapter count or add more judging dimensions before the existing workflow can be installed, understood, and trusted. The first four engineering fixes plus the guided CLI are the recommended initial implementation-plan selection; separate executor-ready plans can be written after scope selection.

## A simple way to use the current version

Assumes Git and `uv` are installed. These are instructions for the user; no installation or live judging was performed during the audit.

```bash
git clone https://github.com/alexhawat/judge-jev.git
cd judge-jev
JUDGE_JEV_RUNTIME=python bash scripts/setup.sh
bash examples/01-judge-a-reply/run.sh
```

The example is a free offline demonstration with canned answers. For a real evaluation, set `TYPESAFE_API_KEY` in the terminal environment, then run from the repository root:

```bash
./scripts/judge-jev run --rubric assistant-reply --input - <<'JSON'
{
  "prompt": "What is the capital of France?",
  "reply": "The capital of France is Paris.",
  "context": "Reference fact: Paris is the capital of France."
}
JSON
```

Replace the three fields with the question, answer, and supporting material you actually want checked. Omitting `--mock` makes this a real TypeSafe API request. To save the result, redirect stdout to an absolute output path. Read `verdict`, `routing_reason`, `deciding_answers`, `confidence`, and `mock` first.

| Exit | Meaning |
|---|---|
| 0 | Pass |
| 1 | Fail verdict; currently also possible from launcher failures, as noted above |
| 2 | Review |
| 3 | Escalate |
| 4 | Skip |
| 10 | Operational failure: no judgment |
| 11 | Usage failure: no judgment |

Until launchers are fixed, confirm that a result JSON exists before treating an exit as a verdict. Review/escalate do not automatically notify anyone. Use stdin or absolute input paths when invoking the tool outside its checkout.

## Evidence, verification, and audit boundaries

- Inspected Python and Rust runtime modules, shared rubrics and schemas, setup/dispatch scripts, tests, examples, hooks, adapter/agent documentation, manifests, and CI. Reviewed recent commit history.
- Latest [CI for the audited commit](https://github.com/alexhawat/judge-jev/actions/runs/35624482656) reported success.
- Locally ran the existing Python suite using cached wheels selected to match `python/uv.lock`, Python 3.14.6, bytecode/cache writing disabled: **136 passed in 0.97 seconds**. This was a direct source test run, not a clean installation/package test. Repository CI uses Python 3.12.
- Ran additional in-memory executions of actual source functions for invalid replay confidence, default-pass gating, uncertain safety screens, unsupported-fact risk, null filtering, overlapping projections, CLI help/no-argument behavior, and mock result generation. Confirmed the outcomes documented above.
- `bash scripts/check-adapter-docs.sh` passed. Executed invalid-runtime launcher and example error paths without any model call.
- Did not rebuild/run Rust locally; Rust findings were established by source tracing and compared with repository CI. Did not execute Windows/PowerShell, real host applications, paid model requests, live API latency/failure experiments, clean package installs, or a dependency CVE audit. No claim of measured judge accuracy, confirmed live prompt-injection exploit, or absence of vulnerabilities is made.
- No repository source files were changed. The audit clone remained clean. No credentials were displayed or saved.

Considered and not reported as bugs: generic local rubric-path access without a hostile-input boundary; configured alternate API endpoints used for local test stubs; intentional first-match/short-circuit routing; intentional matched-rule confidence semantics; lack of uniform mock probabilities as evidence about real model accuracy; and the bytes-per-four token estimate being approximate (it is explicitly documented as a heuristic). Broader floating-point parity deserves differential testing, but no specific extra float counterexample was established here.

# Examples

Six runnable end-to-end examples. Each one is a directory with an `input.json` and
a `run.sh` that judges it, prints the verdict, and exits with the verdict's code.

They run **mocked by default**: offline, free, deterministic, and identical on both
runtimes. Mock answers are canned, so a mocked judgment tests the wiring and is
never a safety check. Add `--live` (with `TYPESAFE_API_KEY` exported) to judge for
real.

## Setup

```bash
bash scripts/setup.sh          # writes .judge-jev/runtime, installs the runtime
```

That is the only prerequisite. `python3` is used to pretty-print the result and is
already required by `scripts/check-parity.sh` and `hooks/post-judge.sh`.

## Run them

```bash
bash examples/run-all.sh                    # all six, checked against expected verdicts
bash examples/01-judge-a-reply/run.sh       # just one
bash examples/01-judge-a-reply/run.sh --live   # same example, real API call, billed
```

Against the other runtime:

```bash
JUDGE_JEV_RUNTIME=rust bash examples/run-all.sh
JUDGE_JEV_RUNTIME=python bash examples/run-all.sh
```

Both must print the same verdicts. That is the same promise `scripts/check-parity.sh`
enforces in CI.

## What each one shows

| Example | Verdict (mock) | Exit | What it demonstrates |
|---------|----------------|------|----------------------|
| [`01-judge-a-reply`](01-judge-a-reply/) | `pass` | 0 | The smallest complete judgment: prompt + reply in, `JudgmentResult` out. |
| [`02-judge-a-trajectory`](02-judge-a-trajectory/) | `pass` | 0 | The other rubric: an agent's tool-use trajectory, judged at `write` stakes with a higher confidence floor. |
| [`03-gate-a-pipeline`](03-gate-a-pipeline/) | `pass` | 0 | The integration shape: branch on the exit code, and keep `10`/`11` out of the verdict branches. Copy this one. |
| [`04-replay-a-judgment`](04-replay-a-judgment/) | `pass` | 0 | Re-route saved answers with no API call — the primitive behind auditing and threshold tuning. |
| [`05-prompt-injection`](05-prompt-injection/) | `escalate` | 3 | Untrusted content trying to steer the judge escalates at the `screen` stage instead of being obeyed. |
| [`06-low-confidence`](06-low-confidence/) | `review` | 2 | An automatic `pass` the judge was not sure enough about is downgraded by the confidence floor. |

`run-all.sh` asserts every one of those exit codes, so an example that stops
demonstrating what it claims fails the build instead of drifting quietly.

## Judging your own input

Write a JSON object with the keys the rubric's `state_filter` declares — everything
else is dropped before the call:

- **assistant-reply**: `prompt`, `reply`, optional `context`
- **agent-trajectory**: `goal`, `steps`, `final_output`

```bash
./scripts/judge-jev run --rubric assistant-reply --input my-reply.json
echo "verdict exit code: $?"
```

Inspect the whole result, not just the verdict — `deciding_answers` tells you which
questions actually produced it:

```bash
./scripts/judge-jev run --rubric assistant-reply --input my-reply.json --mock \
  | jq '{verdict, confidence, deciding_answers, routing_reason}'
```

## Pinned mock answers

Two examples carry a `_mock_answers` block. Mock scores are derived from a hash of
the question name, so an example that has to demonstrate a specific verdict pins
the answers it depends on rather than hoping the hash cooperates. The block is
stripped from the state before any request is built and is ignored entirely in
`--live` mode.

## Examples vs fixtures

`fixtures/` are inputs for the test suite and the parity check — minimal, and
asserted on. `examples/` are for people: real-looking input, explained output, and
a script you can copy into a harness.

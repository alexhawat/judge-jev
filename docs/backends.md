# Judgment backends

`judge-jev run` selects one provider boundary with `--backend` or
`JUDGE_JEV_BACKEND`. An explicit flag wins over the environment; the default is
`typesafe`. Unknown values are usage errors (`11`) before credentials or network
access. `--mock` remains compatible and means the canned `replay` backend; combining
it with a live backend is an error.

| Backend | Credentials | Network | Provenance |
|---|---|---:|---|
| `typesafe` | `TYPESAFE_API_KEY` | yes | live model |
| `cloudflare` | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | yes | live model |
| `replay` | none | no | recorded model or canned demo |

All backends declare Noul, Choice, and Score capabilities. Noul uncertainty is the
distance from `0.5`; Choice and Score confidence are statistics over their returned
distributions. They are distinct quantities. The current verdict policy takes the
minimum over deciding answers as a conservative aggregation assumption; it is not a
calibrated probability that the artifact is correct.

Cloudflare uses the documented `POST /client/v4/accounts/{account}/ai/run` request
with model alias `typesafe/jev` and an `{model,input:{state,questions}}` body. The
rubric still pins `jev-1.13.0`. A response whose resolved model differs from that pin
fails operationally before any verdict is published. Results record `backend`,
`requested_model`, resolved `model`, and whether evidence came from a live model,
recording, or canned demo.

Recorded-answer judging requires a capture whose rubric id, rubric version, rubric
content hash, requested/resolved model, and canonical unredacted filtered-state hash
match the new request. The hash preserves this check when the stored state is
redacted. Records without that content provenance are refused rather than guessed.
This prevents unrelated answers from being presented as fresh evidence. The existing
`replay` command still re-routes a saved judgment without a network call and records
historical usage; it never reports those tokens as newly billed.

Provider retries remain inside each live backend. Retryable failures are 408, 429,
5xx, transport errors, and timeouts, under the shared maximum-retry and total-deadline
policy. Invalid credentials, malformed requests, and malformed successful envelopes
are not retried.

# Provider and optimizer contract research — 2026-09-22

Primary sources checked on 22 September 2026:

- Cloudflare Jev model page: <https://developers.cloudflare.com/ai/models/typesafe/jev/>
- GEPA `optimize`: <https://gepa-ai.github.io/gepa/api/core/optimize/>
- GEPA `GEPAAdapter`: <https://gepa-ai.github.io/gepa/api/core/GEPAAdapter/>
- GEPA `GEPAResult`: <https://gepa-ai.github.io/gepa/api/core/GEPAResult/>
- GEPA package manifest: <https://github.com/gepa-ai/gepa/blob/main/pyproject.toml>

Cloudflare positively documents model alias `typesafe/jev`, all three Jev question
types, and answer/model/usage fields. Its REST example posts to
`/client/v4/accounts/{account}/ai/run` with the alias in the JSON body. The sample
response resolves to `jev-1.13.0`. The page does not promise that this alias will
remain pinned to that version and does not document a Jev-specific request-id field.
The implementation therefore accepts Cloudflare's standard `cf-ray` header when
present and rejects a resolved model that differs from the rubric pin.

Cloudflare examples show the Jev result directly. The generic Cloudflare REST
convention can wrap it in `{success,result,errors}`; the adapter accepts both forms,
requires a successful envelope when one is present, and tests both without making a
credentialed availability probe.

GEPA 0.1.4 documents `optimize(seed_candidate, trainset, valset, adapter, ...)`,
`max_metric_calls`, `max_reflection_cost`, seeded reproducibility, and Pareto candidate
selection. `GEPAAdapter.evaluate` returns aligned outputs/scores and optional
trajectories; `make_reflective_dataset` converts real traces into feedback records.
The integration implements those methods and invokes the actual optional package. It
does not label a custom mutation loop as GEPA.

GEPA 0.1.4's base package declares no dependencies, while its string-model `LM`
imports LiteLLM lazily. The tuning extra therefore includes LiteLLM in GEPA's own
supported `>=1.83,<1.92` range without installing GEPA's unrelated full extra. An
offline contract test executes that lazy import and completion path. LiteLLM usage
and cost are post-response observations; GEPA converts missing values to zero, so
Judge Jev reports zero totals after a reflector call as unavailable rather than free.

Neither verified Jev provider contract exposes a request-level hard output-token
limit. A client can observe usage only after a paid response, which is too late to
guarantee the requested cap; GEPA's reflector cost stopper is also post-response.
The integration therefore offers explicit pre-dispatch call-count limits with retries
disabled and reports spend measurements separately. Strict token/dollar mode refuses
before any provider or reflector call. This is an external contract limitation rather
than a simulated safety claim.

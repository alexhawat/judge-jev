# Setup agent charter

Use this when onboarding a fresh clone.

## Steps

1. Confirm repo root contains `shared/rubrics/` and `python/`, `rust/`.
2. Run `bash scripts/setup.sh` or set `JUDGE_JEV_RUNTIME=python|rust` non-interactively.
3. Verify `.judge-jev/runtime` exists.
4. Smoke: `./scripts/judge-jev run --rubric assistant-reply --input fixtures/assistant-reply-pass.json --mock`
5. If the user will judge live traffic, ensure `TYPESAFE_API_KEY` is set in the environment (never commit it).
6. Install harness adapter from `adapters/<harness>/README.md` when the host is known.

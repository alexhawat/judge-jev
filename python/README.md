# judge-jev (Python runtime)

Production Python runtime using uv, loguru, and typesafe-sdk.

Install from repo root after setup selects `python`:

```bash
cd python && uv sync --dev
uv run judge-jev run --rubric assistant-reply --input ../fixtures/assistant-reply-pass.json --mock
```

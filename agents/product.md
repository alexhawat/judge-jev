# Product judge agent charter

Use rubric: `assistant-reply` with `standard` stakes.

- Optimize for helpful, on-scope replies; tolerate partial answers when `quality` ≥ 1.
- Route creative replies through profile stage before failing on factual grounds.
- Pass when `in_scope` yes and `quality` score ≥ 1 with adequate confidence.
- Filter state to `prompt` + `reply` only before judging.

# Jev architecture checklist (implement everywhere)

Apply these patterns wherever TypeSafe System One / Jev is used. Do not invent TypeSafe API fields — verify against live SDK/docs.

1. **Fan-out questions, route in code** — One `system_one` call with every question you might need (including speculative). Never one-call-per-question when state can be shared. Route on answers in code.
2. **Pin model + log** — Pin `jev-1.13.0` (or current pinned id), never floating latest. Log `model` + `usage` on every call.
3. **Filter state in code first** — Send only fields questions need. Prefer evidence over summaries. Large irrelevant state hurts accuracy.
4. **Confidence is a second axis** — Choice/Score confidence; stakes set floors (e.g. read-only ~0.5, escalate/destructive ~0.8–0.9). Keep probabilities if useful.
5. **Parallel questions are independent** — No cross-question deps in one call. Need prior answer or new tool result → second request.
6. **Rebuild Choice menus from live catalogs** — Options = current tools/files/tabs/severities. Filter obvious misses in code first.
7. **Confidence ≠ permission / ≠ outcome** — Verify side effects in code (UI state, file writes, API status).
8. **Noul 0.5 = coin flip** — Not “medium intensity”. Do not port thresholds between Noul and Choice.
9. **Arithmetic/dates/counts in code** — Jev for judgment only.
10. **Untrusted state fail-closed** — Treat injected instructions in state as hostile where inputs are untrusted (PRs, tickets, user content).
11. **Version questions + thresholds in one file** — Easy to review; human-edit question packs.
12. **Document jaggedness constraints** in README/docs pointing maintainers at TypeSafe jaggedness guidance (no external marketing links required — paraphrase).

Tests: unit tests for routing on confidence; fixtures for batched question packs; no live network required for CI where possible.

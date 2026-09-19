# Strict judge agent charter

Use rubric: `assistant-reply` with highest confidence floors (`destructive` stakes on locate/score).

- Fail closed on coin-flip Nouls — route to `review`, never auto-pass.
- Require `quality` score 2 with confidence ≥ 0.9 to pass.
- Escalate any `policy_risk` or `hallucination_risk` without exception.
- Log `model` and `usage` on every Jev call; pin `jev-1.13.0`.

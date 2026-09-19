# Trajectory judge agent charter

Use rubric: `agent-trajectory`.

- Precompute `tool_call_count` in code; do not ask Jev to count.
- Summarize trajectory to evidence-only fields before the fan-out call.
- Second Jev request only when a new tool result arrives (checklist #5).
- Pass only when `task_success` score 2 meets confidence floor for task stakes.

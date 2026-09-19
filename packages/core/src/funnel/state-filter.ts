import type { FunnelStage, RubricPack } from "../types.js";

const UNTRUSTED_INSTRUCTION_KEYS = [
  "system",
  "system_prompt",
  "instructions",
  "jailbreak",
  "ignore_previous",
] as const;

/** Strip hostile instruction-like keys from untrusted state (checklist #10). */
export function sanitizeUntrustedState(
  state: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(state)) {
    const lower = key.toLowerCase();
    if (UNTRUSTED_INSTRUCTION_KEYS.some((k) => lower.includes(k))) {
      continue;
    }
    out[key] = value;
  }
  return out;
}

/** Keep only fields a stage's questions need (checklist #3). */
export function filterStateForStage(
  state: Record<string, unknown>,
  rubric: RubricPack,
  stage: FunnelStage,
): Record<string, unknown> {
  const allow = rubric.state_fields[stage];
  if (!allow || allow.length === 0) {
    return sanitizeUntrustedState(state);
  }
  const filtered: Record<string, unknown> = {};
  for (const key of allow) {
    if (key in state) {
      filtered[key] = state[key];
    }
  }
  return sanitizeUntrustedState(filtered);
}

/** Merge allowlists across stages for the single fan-out call. */
export function filterStateForRubric(
  state: Record<string, unknown>,
  rubric: RubricPack,
): Record<string, unknown> {
  const keys = new Set<string>();
  for (const fields of Object.values(rubric.state_fields)) {
    fields?.forEach((k) => keys.add(k));
  }
  if (keys.size === 0) {
    return sanitizeUntrustedState(state);
  }
  const filtered: Record<string, unknown> = {};
  for (const key of keys) {
    if (key in state) {
      filtered[key] = state[key];
    }
  }
  return sanitizeUntrustedState(filtered);
}

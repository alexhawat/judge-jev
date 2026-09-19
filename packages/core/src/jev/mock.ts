import type { Questions, SystemOneResult } from "@typesafe-ai/sdk";
import { PINNED_JEV_MODEL } from "../constants.js";
import type { JevClient } from "../types.js";

/** Fixture-backed mock for CI — no live TypeSafe network. */
export function createMockJevClient(
  fixtures: Record<string, SystemOneResult<Questions>>,
): JevClient {
  return {
    async systemOne<Q extends Questions>(
      _state: unknown,
      questions: Q,
    ): Promise<SystemOneResult<Q>> {
      const key = Object.keys(questions).sort().join(",");
      const hit = fixtures[key] ?? fixtures["*"];
      if (!hit) {
        throw new Error(`No mock fixture for questions: ${key}`);
      }
      return {
        model: hit.model ?? PINNED_JEV_MODEL,
        usage: hit.usage ?? { input_tokens: 0, output_tokens: 0 },
        answers: hit.answers,
      } as SystemOneResult<Q>;
    },
  };
}

/** Build a minimal mock result from plain answer objects. */
export function mockResult(
  answers: Record<string, unknown>,
  usage = { input_tokens: 100, output_tokens: 20 },
): SystemOneResult<Questions> {
  return {
    model: PINNED_JEV_MODEL,
    usage,
    answers: answers as SystemOneResult<Questions>["answers"],
  };
}

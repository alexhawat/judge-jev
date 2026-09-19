import { TypeSafeClient } from "@typesafe-ai/sdk";
import type { EntryType, Questions, SystemOneResult } from "@typesafe-ai/sdk";
import { PINNED_JEV_MODEL } from "../constants.js";
import type { JevCallLog, JevClient } from "../types.js";

export interface LiveJevClientOptions {
  apiKey?: string;
  log?: (entry: JevCallLog) => void;
}

/** Live TypeSafe client — pins model and logs usage (checklist #2). */
export function createLiveJevClient(options: LiveJevClientOptions = {}): JevClient {
  const client = new TypeSafeClient({
    apiKey: options.apiKey,
    defaultModel: PINNED_JEV_MODEL,
  });

  return {
    async systemOne<Q extends Questions>(
      state: unknown,
      questions: Q,
    ): Promise<SystemOneResult<Q>> {
      const result = await client.systemOne({
        state: state as EntryType,
        questions,
        model: PINNED_JEV_MODEL,
      });
      options.log?.({
        model: result.model,
        usage: result.usage,
        question_ids: Object.keys(questions),
      });
      return result;
    },
  };
}

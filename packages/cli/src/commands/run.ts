import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  createLiveJevClient,
  createMockJevClient,
  judge,
  loadRubricFromFile,
  mockResult,
} from "@judge-jev/core";
import type { Questions, SystemOneResult } from "@typesafe-ai/sdk";
import { fixturesDir, rubricsDir } from "../paths.js";

export interface RunOptions {
  rubric: string;
  input: string;
  mock?: boolean;
  mockFixture?: string;
  apiKey?: string;
}

function resolveRubricPath(rubricId: string): string {
  return join(rubricsDir(), rubricId, "v1.yaml");
}

function loadInput(path: string): Record<string, unknown> {
  const text = readFileSync(path, "utf8");
  return JSON.parse(text) as Record<string, unknown>;
}

function loadMockFixtures(path: string): Record<string, SystemOneResult<Questions>> {
  const raw = JSON.parse(readFileSync(path, "utf8")) as {
    default?: Record<string, unknown>;
    by_question_key?: Record<string, Record<string, unknown>>;
  };
  const fixtures: Record<string, SystemOneResult<Questions>> = {};
  if (raw.default) {
    fixtures["*"] = mockResult(raw.default);
  }
  if (raw.by_question_key) {
    for (const [key, answers] of Object.entries(raw.by_question_key)) {
      fixtures[key] = mockResult(answers);
    }
  }
  return fixtures;
}

export async function runCommand(options: RunOptions): Promise<void> {
  const rubricPath = resolveRubricPath(options.rubric);
  const rubric = loadRubricFromFile(rubricPath);
  const state = loadInput(options.input);

  const log = (entry: { model: string; usage: unknown }) => {
    console.error(`[jev] model=${entry.model} usage=${JSON.stringify(entry.usage)}`);
  };

  let client;
  if (options.mock) {
    const fixturePath =
      options.mockFixture ?? join(fixturesDir(), "jev-responses", `${options.rubric}.json`);
    client = createMockJevClient(loadMockFixtures(fixturePath));
  } else {
    client = createLiveJevClient({ apiKey: options.apiKey, log });
  }

  const result = await judge({ rubric, state }, { client, log });

  console.log(JSON.stringify(result, null, 2));
}

import { readFileSync } from "node:fs";
import {
  createMockJevClient,
  judge,
  loadRubricFromString,
  mockResult,
} from "@judge-jev/core";
import type { Questions, SystemOneResult } from "@typesafe-ai/sdk";

export interface ReplayOptions {
  fixture: string;
}

interface ReplayFixture {
  rubric_yaml: string;
  state: Record<string, unknown>;
  jev_answers: Record<string, unknown>;
  expected_route?: string;
}

export async function replayCommand(options: ReplayOptions): Promise<void> {
  const fixture = JSON.parse(readFileSync(options.fixture, "utf8")) as ReplayFixture;
  const rubric = loadRubricFromString(fixture.rubric_yaml, options.fixture);
  const fixtures: Record<string, SystemOneResult<Questions>> = {
    "*": mockResult(fixture.jev_answers),
  };
  const client = createMockJevClient(fixtures);
  const result = await judge({ rubric, state: fixture.state }, { client });

  console.log(JSON.stringify(result, null, 2));

  if (fixture.expected_route && result.route !== fixture.expected_route) {
    console.error(
      `Expected route ${fixture.expected_route}, got ${result.route}`,
    );
    process.exitCode = 1;
  }
}

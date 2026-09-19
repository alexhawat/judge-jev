import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { loadRubricFromFile, questionsByStage } from "../src/rubric/load.js";
import { buildQuestions } from "../src/rubric/build-questions.js";
import { PINNED_JEV_MODEL } from "../src/constants.js";

const repoRoot = join(import.meta.dirname, "..", "..", "..");

describe("rubric loader", () => {
  it("loads assistant-reply and pins model", () => {
    const rubric = loadRubricFromFile(
      join(repoRoot, "rubrics/assistant-reply/v1.yaml"),
    );
    expect(rubric.id).toBe("assistant-reply");
    expect(rubric.model).toBe(PINNED_JEV_MODEL);
    expect(Object.keys(rubric.questions).length).toBeGreaterThan(3);
  });

  it("groups questions by stage", () => {
    const rubric = loadRubricFromFile(
      join(repoRoot, "rubrics/agent-trajectory/v1.yaml"),
    );
    const byStage = questionsByStage(rubric);
    expect(byStage.get("screen")).toContain("has_trajectory");
  });

  it("builds SDK questions for fan-out", () => {
    const rubric = loadRubricFromFile(
      join(repoRoot, "rubrics/assistant-reply/v1.yaml"),
    );
    const q = buildQuestions(rubric);
    expect(q.in_scope?.type).toBe("noul");
    expect(q.quality?.type).toBe("score");
  });
});

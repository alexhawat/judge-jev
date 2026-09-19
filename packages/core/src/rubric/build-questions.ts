import { choice, noul, score } from "@typesafe-ai/sdk";
import type { Questions, Question } from "@typesafe-ai/sdk";
import type { RubricPack, RubricQuestionDef } from "../types.js";

function buildQuestion(def: RubricQuestionDef): Question {
  switch (def.type) {
    case "noul":
      return noul(def.instructions);
    case "choice":
      if (!def.criteria || Array.isArray(def.criteria)) {
        throw new Error(`Choice question requires object criteria`);
      }
      return choice(def.instructions, def.criteria);
    case "score":
      if (!def.criteria || !Array.isArray(def.criteria) || def.criteria.length < 2) {
        throw new Error(`Score question requires array criteria (min 2)`);
      }
      return score(def.instructions, def.criteria as [string, string, ...string[]]);
    default:
      throw new Error(`Unknown question type`);
  }
}

/** Build SDK Questions map for one fan-out systemOne call. */
export function buildQuestions(rubric: RubricPack): Questions {
  const questions: Record<string, Question> = {};
  for (const [id, def] of Object.entries(rubric.questions)) {
    questions[id] = buildQuestion(def);
  }
  return questions;
}

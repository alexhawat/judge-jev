import type {
  FunnelStage,
  RubricPack,
  RubricThresholds,
  StageOutcome,
} from "../types.js";
import { noulIsCoinFlip, noulIsNo, noulIsYes } from "./routing.js";

const STAGE_ORDER: FunnelStage[] = [
  "screen",
  "profile",
  "locate",
  "score",
  "route",
];

function stageQuestionIds(rubric: RubricPack, stage: FunnelStage): string[] {
  return Object.entries(rubric.questions)
    .filter(([, q]) => q.stage === stage)
    .map(([id]) => id);
}

function evaluateScreen(
  answers: Record<string, unknown>,
  thresholds: RubricThresholds,
  ids: string[],
): StageOutcome {
  const notes: string[] = [];
  let passed = true;
  for (const id of ids) {
    const a = answers[id] as { type?: string; noul?: number } | undefined;
    if (a?.type === "noul" && typeof a.noul === "number") {
      if (noulIsCoinFlip(a.noul)) {
        notes.push(`${id}: coin-flip noul (${a.noul.toFixed(2)}) — defer`);
        passed = false;
      } else if (id.includes("skip") || id.includes("empty")) {
        if (noulIsYes(a.noul, thresholds)) {
          notes.push(`${id}: empty/skippable`);
          passed = false;
        }
      } else if (id.includes("in_scope") || id.includes("judgeable")) {
        if (noulIsNo(a.noul, thresholds)) {
          notes.push(`${id}: out of scope`);
          passed = false;
        }
      }
    }
  }
  return { stage: "screen", passed, notes };
}

function evaluateProfile(
  answers: Record<string, unknown>,
  ids: string[],
): StageOutcome {
  const notes: string[] = [];
  for (const id of ids) {
    const a = answers[id] as { choice?: string; confidence?: number } | undefined;
    if (a?.choice) {
      notes.push(`${id}: ${a.choice}${a.confidence != null ? ` (${a.confidence.toFixed(2)})` : ""}`);
    }
  }
  return { stage: "profile", passed: true, notes };
}

function evaluateLocate(
  answers: Record<string, unknown>,
  thresholds: RubricThresholds,
  ids: string[],
): StageOutcome {
  const notes: string[] = [];
  let passed = true;
  for (const id of ids) {
    const a = answers[id] as { type?: string; noul?: number } | undefined;
    if (a?.type === "noul" && typeof a.noul === "number" && noulIsYes(a.noul, thresholds)) {
      notes.push(`${id}: issue detected (${a.noul.toFixed(2)})`);
      passed = false;
    }
  }
  return { stage: "locate", passed, notes };
}

function evaluateScore(
  answers: Record<string, unknown>,
  ids: string[],
): StageOutcome {
  const notes: string[] = [];
  for (const id of ids) {
    const a = answers[id] as { score?: number; confidence?: number } | undefined;
    if (typeof a?.score === "number") {
      notes.push(`${id}: score ${a.score.toFixed(2)}`);
    }
  }
  return { stage: "score", passed: true, notes };
}

/** Evaluate logical funnel stages from batched answers (routing in code). */
export function evaluateStages(
  rubric: RubricPack,
  answers: Record<string, unknown>,
): StageOutcome[] {
  const outcomes: StageOutcome[] = [];
  for (const stage of STAGE_ORDER) {
    if (stage === "route") continue;
    const ids = stageQuestionIds(rubric, stage);
    if (ids.length === 0) continue;

    switch (stage) {
      case "screen":
        outcomes.push(evaluateScreen(answers, rubric.thresholds, ids));
        break;
      case "profile":
        outcomes.push(evaluateProfile(answers, ids));
        break;
      case "locate":
        outcomes.push(evaluateLocate(answers, rubric.thresholds, ids));
        break;
      case "score":
        outcomes.push(evaluateScore(answers, ids));
        break;
    }
  }
  return outcomes;
}

/** Early skip when screen stage fails clearly. */
export function shouldSkipAfterScreen(stages: StageOutcome[]): boolean {
  const screen = stages.find((s) => s.stage === "screen");
  return screen !== undefined && !screen.passed;
}

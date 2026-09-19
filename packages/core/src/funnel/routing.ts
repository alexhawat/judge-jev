import { NOUL_COIN_FLIP } from "../constants.js";
import type {
  ConfidenceFloors,
  RouteDecision,
  RouteStakes,
  RubricPack,
  RubricRouteRule,
  RubricThresholds,
} from "../types.js";

export function confidenceFloor(
  floors: ConfidenceFloors,
  stakes: RouteStakes,
): number {
  return floors[stakes];
}

/** Noul yes/no thresholds are separate from choice confidence (checklist #8). */
export function noulIsYes(value: number, thresholds: RubricThresholds): boolean {
  return value >= thresholds.noul_yes;
}

export function noulIsNo(value: number, thresholds: RubricThresholds): boolean {
  return value <= thresholds.noul_no;
}

export function noulIsCoinFlip(value: number): boolean {
  return Math.abs(value - NOUL_COIN_FLIP) < 0.15;
}

function getAnswerField(answer: unknown): unknown {
  if (!answer || typeof answer !== "object") return undefined;
  const a = answer as Record<string, unknown>;
  if ("noul" in a) return a.noul;
  if ("choice" in a) return a.choice;
  if ("score" in a) return a.score;
  if ("confidence" in a) return a.confidence;
  return undefined;
}

function matchesCondition(
  answers: Record<string, unknown>,
  key: string,
  expected: unknown,
  rubric: RubricPack,
): boolean {
  const answer = answers[key];
  if (expected === "yes") {
    const noul = (answer as { noul?: number })?.noul;
    return typeof noul === "number" && noulIsYes(noul, rubric.thresholds);
  }
  if (expected === "no") {
    const noul = (answer as { noul?: number })?.noul;
    return typeof noul === "number" && noulIsNo(noul, rubric.thresholds);
  }
  if (expected === "coin_flip") {
    const noul = (answer as { noul?: number })?.noul;
    return typeof noul === "number" && noulIsCoinFlip(noul);
  }
  const field = getAnswerField(answer);
  return field === expected;
}

/** Apply rubric route rules in order; first match wins. */
export function resolveRoute(
  rubric: RubricPack,
  answers: Record<string, unknown>,
  defaultStakes: RouteStakes = "standard",
): { route: RouteDecision; reason: string; confidence_floor: number } {
  const floor = confidenceFloor(rubric.confidence_floors, defaultStakes);

  for (const rule of rubric.routes) {
    if (ruleMatches(rule, answers, rubric, floor)) {
      return { route: rule.route, reason: rule.reason, confidence_floor: floor };
    }
  }

  return {
    route: "review",
    reason: "No route rule matched; defaulting to human review",
    confidence_floor: floor,
  };
}

function ruleMatches(
  rule: RubricRouteRule,
  answers: Record<string, unknown>,
  rubric: RubricPack,
  floor: number,
): boolean {
  for (const [key, expected] of Object.entries(rule.when)) {
    if (key.endsWith("_min_confidence")) {
      const qid = key.replace(/_min_confidence$/, "");
      const answer = answers[qid] as { confidence?: number } | undefined;
      const conf = answer?.confidence;
      if (typeof conf !== "number" || conf < floor) {
        return false;
      }
      continue;
    }
    if (!matchesCondition(answers, key, expected, rubric)) {
      return false;
    }
  }
  return true;
}

/** Counts, dates, and arithmetic stay in code — not Jev (checklist #9). */
export function countToolCalls(trajectory: unknown): number {
  if (!Array.isArray(trajectory)) return 0;
  return trajectory.filter(
    (step) =>
      step &&
      typeof step === "object" &&
      (step as { type?: string }).type === "tool_call",
  ).length;
}

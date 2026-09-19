import { describe, expect, it } from "vitest";
import {
  confidenceFloor,
  noulIsCoinFlip,
  noulIsYes,
  resolveRoute,
  countToolCalls,
} from "../src/funnel/routing.js";
import { loadRubricFromString } from "../src/rubric/load.js";

const minimalRubric = loadRubricFromString(`
id: test
version: "1"
description: test
model: jev-1.13.0
confidence_floors:
  read_only: 0.5
  standard: 0.65
  escalate: 0.8
  destructive: 0.9
thresholds:
  noul_yes: 0.75
  noul_no: 0.25
state_fields:
  screen: [a]
questions:
  flag:
    type: noul
    stage: locate
    instructions: "flag?"
  grade:
    type: score
    stage: score
    stakes: standard
    instructions: "grade?"
    criteria: [bad, ok, good]
routes:
  - when:
      flag: yes
    route: escalate
    reason: flagged
  - when:
      grade: 2
      grade_min_confidence: true
    route: pass
    reason: good
`);

describe("routing", () => {
  it("applies confidence floors by stakes", () => {
    expect(confidenceFloor(minimalRubric.confidence_floors, "escalate")).toBe(0.8);
  });

  it("treats noul 0.5 as coin flip", () => {
    expect(noulIsCoinFlip(0.5)).toBe(true);
    expect(noulIsCoinFlip(0.9)).toBe(false);
  });

  it("routes escalate on noul yes", () => {
    const route = resolveRoute(minimalRubric, {
      flag: { type: "noul", noul: 0.95 },
      grade: { type: "score", score: 1, confidence: 0.9 },
    });
    expect(route.route).toBe("escalate");
  });

  it("requires confidence for pass route", () => {
    const lowConf = resolveRoute(minimalRubric, {
      flag: { type: "noul", noul: 0.1 },
      grade: { type: "score", score: 2, confidence: 0.4 },
    });
    expect(lowConf.route).toBe("review");

    const highConf = resolveRoute(minimalRubric, {
      flag: { type: "noul", noul: 0.1 },
      grade: { type: "score", score: 2, confidence: 0.9 },
    });
    expect(highConf.route).toBe("pass");
  });

  it("counts tool calls in code", () => {
    expect(
      countToolCalls([
        { type: "message" },
        { type: "tool_call" },
        { type: "tool_call" },
      ]),
    ).toBe(2);
  });

  it("uses separate noul yes threshold", () => {
    expect(noulIsYes(0.8, minimalRubric.thresholds)).toBe(true);
    expect(noulIsYes(0.5, minimalRubric.thresholds)).toBe(false);
  });
});

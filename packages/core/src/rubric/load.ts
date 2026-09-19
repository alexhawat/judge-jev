import { readFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";
import { PINNED_JEV_MODEL } from "../constants.js";
import type { RubricPack } from "../types.js";

function assertRubric(raw: unknown, source: string): RubricPack {
  if (!raw || typeof raw !== "object") {
    throw new Error(`Invalid rubric at ${source}: expected mapping`);
  }
  const r = raw as Record<string, unknown>;
  const required = [
    "id",
    "version",
    "description",
    "confidence_floors",
    "thresholds",
    "questions",
    "state_fields",
    "routes",
  ] as const;
  for (const key of required) {
    if (!(key in r)) {
      throw new Error(`Invalid rubric at ${source}: missing "${key}"`);
    }
  }
  const model = typeof r.model === "string" ? r.model : PINNED_JEV_MODEL;
  if (model !== PINNED_JEV_MODEL) {
    throw new Error(
      `Rubric ${String(r.id)} pins model "${model}"; expected "${PINNED_JEV_MODEL}"`,
    );
  }
  return {
    id: String(r.id),
    version: String(r.version),
    description: String(r.description),
    model,
    confidence_floors: r.confidence_floors as RubricPack["confidence_floors"],
    thresholds: r.thresholds as RubricPack["thresholds"],
    questions: r.questions as RubricPack["questions"],
    state_fields: r.state_fields as RubricPack["state_fields"],
    routes: r.routes as RubricPack["routes"],
  };
}

/** Load and validate a versioned rubric YAML pack. */
export function loadRubricFromFile(path: string): RubricPack {
  const text = readFileSync(path, "utf8");
  return loadRubricFromString(text, path);
}

/** Parse rubric YAML from string (tests/fixtures). */
export function loadRubricFromString(yaml: string, source = "<string>"): RubricPack {
  const raw = parseYaml(yaml);
  return assertRubric(raw, source);
}

/** List question ids grouped by funnel stage. */
export function questionsByStage(rubric: RubricPack): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const [id, q] of Object.entries(rubric.questions)) {
    const list = map.get(q.stage) ?? [];
    list.push(id);
    map.set(q.stage, list);
  }
  return map;
}

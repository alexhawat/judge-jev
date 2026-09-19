import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { loadRubricFromFile, questionsByStage } from "@judge-jev/core";
import { rubricsDir } from "../paths.js";

function findRubricFiles(): { id: string; path: string }[] {
  const root = rubricsDir();
  const out: { id: string; path: string }[] = [];
  for (const id of readdirSync(root)) {
    const dir = join(root, id);
    if (!statSync(dir).isDirectory()) continue;
    for (const file of readdirSync(dir)) {
      if (file.endsWith(".yaml") || file.endsWith(".yml")) {
        out.push({ id, path: join(dir, file) });
      }
    }
  }
  return out;
}

export function rubricList(): void {
  const files = findRubricFiles();
  for (const { path } of files) {
    const rubric = loadRubricFromFile(path);
    console.log(`${rubric.id}@${rubric.version}\t${path}`);
  }
}

export function rubricShow(rubricId: string): void {
  const hit = findRubricFiles().find((f) => f.id === rubricId);
  if (!hit) {
    throw new Error(`Unknown rubric: ${rubricId}`);
  }
  const text = readFileSync(hit.path, "utf8");
  console.log(text);
}

export function rubricValidate(rubricId?: string): void {
  const files = rubricId
    ? findRubricFiles().filter((f) => f.id === rubricId)
    : findRubricFiles();
  if (files.length === 0) {
    throw new Error(rubricId ? `Unknown rubric: ${rubricId}` : "No rubrics found");
  }
  for (const { path } of files) {
    const rubric = loadRubricFromFile(path);
    const byStage = questionsByStage(rubric);
    console.log(`OK ${rubric.id}@${rubric.version} — ${rubric.model}, ${byStage.size} stages`);
  }
}

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

/** Repo root (packages/cli/dist → ../../..). */
export function repoRoot(): string {
  return join(__dirname, "..", "..", "..");
}

export function rubricsDir(): string {
  return join(repoRoot(), "rubrics");
}

export function fixturesDir(): string {
  return join(repoRoot(), "fixtures");
}

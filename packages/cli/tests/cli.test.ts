import { execFileSync } from "node:child_process";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..", "..");
const bin = join(repoRoot, "packages/cli/dist/bin.js");

describe("judge-jev CLI", () => {
  it("run --mock on assistant-reply fixture", () => {
    const out = execFileSync(
      process.execPath,
      [
        bin,
        "run",
        "--rubric",
        "assistant-reply",
        "--input",
        join(repoRoot, "fixtures/assistant-reply-pass.json"),
        "--mock",
      ],
      { encoding: "utf8" },
    );
    const result = JSON.parse(out);
    expect(result.route).toBe("pass");
    expect(result.model).toBe("jev-1.13.0");
  });

  it("rubric list includes packs", () => {
    const out = execFileSync(process.execPath, [bin, "rubric", "list"], {
      encoding: "utf8",
    });
    expect(out).toContain("assistant-reply");
    expect(out).toContain("agent-trajectory");
  });
});

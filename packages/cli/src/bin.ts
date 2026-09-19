#!/usr/bin/env node
import { replayCommand } from "./commands/replay.js";
import { rubricList, rubricShow, rubricValidate } from "./commands/rubric.js";
import { runCommand } from "./commands/run.js";

function usage(): void {
  console.log(`judge-jev — Jev-native LLM output judge

Usage:
  judge-jev run --rubric <id> --input <path> [--mock] [--mock-fixture <path>]
  judge-jev rubric list|show <id>|validate [id]
  judge-jev replay --fixture <path>
`);
}

function parseArgs(argv: string[]): {
  command: string;
  sub?: string;
  flags: Record<string, string | boolean>;
  positional: string[];
} {
  const args = argv.slice(2);
  const command = args[0] ?? "";
  const flags: Record<string, string | boolean> = {};
  const positional: string[] = [];
  let sub: string | undefined;
  let i = 1;

  if (command === "rubric" && args[1] && !args[1].startsWith("--")) {
    sub = args[1];
    i = 2;
  }

  for (; i < args.length; i++) {
    const arg = args[i]!;
    if (arg.startsWith("--")) {
      const key = arg.slice(2);
      const next = args[i + 1];
      if (next && !next.startsWith("--")) {
        flags[key] = next;
        i++;
      } else {
        flags[key] = true;
      }
    } else if (command !== "rubric" || sub !== undefined) {
      positional.push(arg);
    }
  }
  return { command, sub, flags, positional };
}

async function main(): Promise<void> {
  const { command, sub, flags, positional } = parseArgs(process.argv);

  try {
    switch (command) {
      case "run":
        await runCommand({
          rubric: String(flags.rubric ?? ""),
          input: String(flags.input ?? ""),
          mock: Boolean(flags.mock),
          mockFixture: flags["mock-fixture"] as string | undefined,
          apiKey: flags["api-key"] as string | undefined,
        });
        break;
      case "rubric":
        if (sub === "list") rubricList();
        else if (sub === "show") rubricShow(positional[0] ?? "");
        else if (sub === "validate") rubricValidate(positional[0]);
        else usage();
        break;
      case "replay":
        await replayCommand({ fixture: String(flags.fixture ?? positional[0] ?? "") });
        break;
      default:
        usage();
        process.exitCode = command ? 1 : 0;
    }
  } catch (err) {
    console.error(err instanceof Error ? err.message : err);
    process.exitCode = 1;
  }
}

main();

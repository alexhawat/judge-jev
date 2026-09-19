import { buildQuestions } from "../rubric/build-questions.js";
import { filterStateForRubric } from "./state-filter.js";
import { evaluateStages, shouldSkipAfterScreen } from "./stages.js";
import { resolveRoute } from "./routing.js";
import type {
  JudgeInput,
  JudgeOptions,
  JudgmentResult,
  RouteDecision,
} from "../types.js";

/** Run rubric → fan-out Jev call → staged funnel → JudgmentResult. */
export async function judge(
  input: JudgeInput,
  options: JudgeOptions,
): Promise<JudgmentResult> {
  const { rubric, state } = input;
  const filteredState = filterStateForRubric(state, rubric);
  const questions = buildQuestions(rubric);

  const result = await options.client.systemOne(filteredState, questions);

  options.log?.({
    model: result.model,
    usage: result.usage,
    question_ids: Object.keys(questions),
  });

  const answers = result.answers as Record<string, unknown>;
  const stages = evaluateStages(rubric, answers);

  let route: RouteDecision;
  let reason: string;
  let confidence_floor: number;

  if (shouldSkipAfterScreen(stages)) {
    route = "skip";
    reason = stages.find((s) => s.stage === "screen")?.notes.join("; ") ?? "Screen failed";
    confidence_floor = rubric.confidence_floors.read_only;
  } else {
    const resolved = resolveRoute(rubric, answers);
    route = resolved.route;
    reason = resolved.reason;
    confidence_floor = resolved.confidence_floor;
  }

  const side_effects_verified =
    options.verifySideEffects?.(input, answers) ?? true;

  return {
    rubric_id: rubric.id,
    rubric_version: rubric.version,
    route,
    reason,
    stages,
    answers,
    model: result.model,
    usage: result.usage,
    confidence_floor,
    side_effects_verified,
  };
}

import type { Questions, SystemOneResult, Usage } from "@typesafe-ai/sdk";

/** Stakes tier sets confidence floors for routing (checklist #4). */
export type RouteStakes = "read_only" | "standard" | "escalate" | "destructive";

/** Logical funnel stages; routing happens in code after one fan-out call. */
export type FunnelStage = "screen" | "profile" | "locate" | "score" | "route";

/** Terminal route decision. */
export type RouteDecision = "pass" | "fail" | "review" | "escalate" | "skip";

export interface ConfidenceFloors {
  read_only: number;
  standard: number;
  escalate: number;
  destructive: number;
}

export interface RubricQuestionDef {
  type: "noul" | "choice" | "score";
  instructions: string;
  criteria?: Record<string, string> | string[];
  stage: FunnelStage;
  /** When set, route uses this stakes tier for confidence floor on choice/score answers. */
  stakes?: RouteStakes;
}

export interface RubricRouteRule {
  /** Route when all conditions match. */
  when: Record<string, unknown>;
  route: RouteDecision;
  reason: string;
}

export interface RubricThresholds {
  /** Noul yes threshold (separate from choice/score confidence — checklist #8). */
  noul_yes: number;
  noul_no: number;
}

export interface RubricPack {
  id: string;
  version: string;
  description: string;
  model: string;
  confidence_floors: ConfidenceFloors;
  thresholds: RubricThresholds;
  /** All questions for one fan-out systemOne call (checklist #1, #11). */
  questions: Record<string, RubricQuestionDef>;
  /** Per-stage state field allowlists for filtering (checklist #3). */
  state_fields: Partial<Record<FunnelStage, string[]>>;
  routes: RubricRouteRule[];
}

export interface StageOutcome {
  stage: FunnelStage;
  passed: boolean;
  notes: string[];
}

export interface JudgmentResult {
  rubric_id: string;
  rubric_version: string;
  route: RouteDecision;
  reason: string;
  stages: StageOutcome[];
  /** Raw answers keyed by question id. */
  answers: Record<string, unknown>;
  /** Model id returned by Jev (logged per checklist #2). */
  model: string;
  usage: Usage;
  /** Confidence floor applied at route stage. */
  confidence_floor: number;
  /** Whether side effects were verified in code (checklist #7). */
  side_effects_verified: boolean;
}

export interface JudgeInput {
  /** Full untrusted payload; filtered before Jev calls. */
  state: Record<string, unknown>;
  rubric: RubricPack;
}

export interface JevCallLog {
  model: string;
  usage: Usage;
  question_ids: string[];
}

export interface JevClient {
  systemOne<Q extends Questions>(
    state: unknown,
    questions: Q,
  ): Promise<SystemOneResult<Q>>;
}

export interface JudgeOptions {
  client: JevClient;
  /** Optional logger for model + usage (checklist #2). */
  log?: (entry: JevCallLog) => void;
  /** Code-side side-effect verification hook (checklist #7). */
  verifySideEffects?: (input: JudgeInput, answers: Record<string, unknown>) => boolean;
}

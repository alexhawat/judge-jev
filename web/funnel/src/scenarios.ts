/** Play-through scenarios derived from repo fixtures + assistant-reply rubric routing. */

export interface MockAnswer {
  type: string;
  noul?: number;
  choice?: string;
  score?: number;
  confidence?: number;
}

export interface PlayStep {
  nodeId: string;
  /** Full dwell on deciding stages; batched stages use shorter flash. */
  deciding: boolean;
  dwellMs: number;
  /** Prominent panel copy for this step — makes scenarios visually distinct mid-play. */
  stepNote: string;
  /** Mock answer keys to surface in the panel on this step. */
  showAnswers?: string[];
}

export interface PlayScenario {
  id: string;
  label: string;
  fixtureFile: string;
  summary: string;
  verdict: string;
  verdictNodeId: string;
  routingReason: string;
  confidence: number;
  decidingAnswers: string[];
  mockAnswers: Record<string, MockAnswer>;
  /** Ordered node ids for cumulative path highlight during play. */
  decidingPath: string[];
  steps: PlayStep[];
}

const SETUP: PlayStep[] = [
  {
    nodeId: 'input',
    deciding: true,
    dwellMs: 850,
    stepNote: 'Load JSON artifact under test (prompt, reply, optional context).',
  },
  {
    nodeId: 'state-filter',
    deciding: true,
    dwellMs: 850,
    stepNote: 'Keep only rubric state_filter keys; strip _mock_answers before TypeSafe.',
  },
];

const JEV_BATCHED = (extra: string): PlayStep => ({
  nodeId: 'jev-call',
  deciding: true,
  dwellMs: 900,
  stepNote: `One batched system_one (jev-1.13.0). ${extra}`,
});

const ANSWERS: PlayStep = {
  nodeId: 'answers',
  deciding: true,
  dwellMs: 800,
  stepNote: 'Normalize noul / choice / score answers from the batched response.',
};

const ROUTE: PlayStep = {
  nodeId: 'route',
  deciding: true,
  dwellMs: 950,
  stepNote: 'Evaluate YAML rules in order; first matching all conditions wins.',
};

function tail(verdictNodeId: string, reason: string, viaFloor: boolean): PlayStep[] {
  const out: PlayStep[] = [];
  if (viaFloor) {
    out.push({
      nodeId: 'confidence-floor',
      deciding: true,
      dwellMs: 950,
      stepNote:
        verdictNodeId === 'verdict-review'
          ? 'pass rule matched but confidence min(deciding) < read_only floor 0.50 → review.'
          : 'confidence min(deciding) meets read_only floor 0.50 — automatic verdict stands.',
    });
  }
  out.push({
    nodeId: verdictNodeId,
    deciding: true,
    dwellMs: 1000,
    stepNote: reason,
  });
  out.push({
    nodeId: 'result',
    deciding: true,
    dwellMs: 800,
    stepNote: 'Emit JudgmentResult JSON on stdout with verdict, confidence, deciding_answers.',
  });
  return out;
}

export const PLAY_SCENARIOS: PlayScenario[] = [
  {
    id: 'assistant-reply-pass',
    label: 'pass — helpful Paris answer',
    fixtureFile: 'assistant-reply-pass.json',
    summary: 'Clear factual reply; helpfulness and coherence clear pass thresholds with strong confidence.',
    verdict: 'pass',
    verdictNodeId: 'verdict-pass',
    routingReason: 'Meets helpfulness and coherence thresholds.',
    confidence: 0.92,
    decidingAnswers: ['score.helpfulness', 'score.coherence'],
    mockAnswers: {
      'score.helpfulness': { type: 'score', score: 3.2, confidence: 0.92 },
      'score.coherence': { type: 'score', score: 3.5, confidence: 0.94 },
    },
    decidingPath: [
      'input',
      'state-filter',
      'jev-call',
      'score',
      'answers',
      'route',
      'confidence-floor',
      'verdict-pass',
      'result',
    ],
    steps: [
      ...SETUP,
      JEV_BATCHED('Score stage decides: helpfulness ≥ 2 and coherence ≥ 2.'),
      {
        nodeId: 'score',
        deciding: true,
        dwellMs: 950,
        stepNote: 'score.helpfulness 3.2 ≥ 2.0 · score.coherence 3.5 ≥ 2.0 — pass rule matches.',
        showAnswers: ['score.helpfulness', 'score.coherence'],
      },
      ANSWERS,
      {
        ...ROUTE,
        stepNote: 'pass rule matches first: helpfulness ≥ 2 AND coherence ≥ 2.',
      },
      ...tail('verdict-pass', 'Meets helpfulness and coherence thresholds.', true),
    ],
  },
  {
    id: 'assistant-reply-fail',
    label: 'fail — terse refusal',
    fixtureFile: 'assistant-reply-fail.json',
    summary: 'Bare refusal with minimal helpfulness; fixture _mock_answers trigger fail rule.',
    verdict: 'fail',
    verdictNodeId: 'verdict-fail',
    routingReason: 'Unhelpful refusal without adequate explanation.',
    confidence: 0.88,
    decidingAnswers: ['profile.intent', 'score.helpfulness'],
    mockAnswers: {
      'profile.intent': { type: 'choice', choice: 'refusal', confidence: 0.88 },
      'score.helpfulness': { type: 'score', score: 0.4, confidence: 0.91 },
    },
    decidingPath: [
      'input',
      'state-filter',
      'jev-call',
      'profile',
      'score',
      'answers',
      'route',
      'confidence-floor',
      'verdict-fail',
      'result',
    ],
    steps: [
      ...SETUP,
      JEV_BATCHED('Profile + score stages decide this verdict.'),
      {
        nodeId: 'profile',
        deciding: true,
        dwellMs: 950,
        stepNote: 'profile.intent == refusal (confidence 0.88).',
        showAnswers: ['profile.intent'],
      },
      {
        nodeId: 'score',
        deciding: true,
        dwellMs: 950,
        stepNote: 'score.helpfulness 0.4 < 1.5 — paired with refusal → fail rule.',
        showAnswers: ['score.helpfulness'],
      },
      ANSWERS,
      {
        ...ROUTE,
        stepNote: 'fail rule: refusal AND helpfulness < 1.5.',
      },
      ...tail('verdict-fail', 'Unhelpful refusal without adequate explanation.', true),
    ],
  },
  {
    id: 'assistant-reply-escalate-flagged',
    label: 'escalate — route flag',
    fixtureFile: 'assistant-reply-escalate-flagged.json',
    summary: 'Medical-adjacent reply; route.escalate noul ≥ 0.7 (fixture _mock_answers).',
    verdict: 'escalate',
    verdictNodeId: 'verdict-escalate',
    routingReason: 'Model flagged this reply for human review.',
    confidence: 0.7,
    decidingAnswers: ['route.escalate'],
    mockAnswers: {
      'route.escalate': { type: 'noul', noul: 0.85 },
    },
    decidingPath: [
      'input',
      'state-filter',
      'jev-call',
      'route-q',
      'answers',
      'route',
      'verdict-escalate',
      'result',
    ],
    steps: [
      ...SETUP,
      JEV_BATCHED('Route-stage question decides; other stages batched but not shown.'),
      {
        nodeId: 'route-q',
        deciding: true,
        dwellMs: 950,
        stepNote: 'route.escalate noul 0.85 ≥ 0.7 — human review before acting on reply.',
        showAnswers: ['route.escalate'],
      },
      ANSWERS,
      {
        ...ROUTE,
        stepNote: 'escalate rule matches: route.escalate ≥ 0.7 (before pass/fail rules).',
      },
      ...tail('verdict-escalate', 'Model flagged this reply for human review.', false),
    ],
  },
  {
    id: 'assistant-reply-injection',
    label: 'escalate — prompt injection',
    fixtureFile: 'assistant-reply-injection.json',
    summary: 'Untrusted prompt tries to override judge; screen.injection ≥ 0.7 → escalate.',
    verdict: 'escalate',
    verdictNodeId: 'verdict-escalate',
    routingReason: 'Possible prompt injection in untrusted state.',
    confidence: 0.64,
    decidingAnswers: ['screen.injection'],
    mockAnswers: {
      'screen.injection': { type: 'noul', noul: 0.82 },
    },
    decidingPath: [
      'input',
      'state-filter',
      'jev-call',
      'screen',
      'answers',
      'route',
      'verdict-escalate',
      'result',
    ],
    steps: [
      ...SETUP,
      JEV_BATCHED(
        'Profile, locate, score, route-q batched in system_one — not deciding; screen.injection wins early.',
      ),
      {
        nodeId: 'screen',
        deciding: true,
        dwellMs: 1000,
        stepNote: 'screen.injection noul 0.82 ≥ 0.7 — injection rule fires before profile/locate/score matter.',
        showAnswers: ['screen.injection'],
      },
      ANSWERS,
      {
        ...ROUTE,
        stepNote: 'Second rule in YAML: injection ≥ 0.7 → escalate (skips later rules).',
      },
      ...tail('verdict-escalate', 'Possible prompt injection in untrusted state.', false),
    ],
  },
  {
    id: 'assistant-reply-low-confidence',
    label: 'review — confidence floor',
    fixtureFile: 'assistant-reply-low-confidence.json',
    summary: 'Scores pass thresholds but helpfulness confidence 0.40 < read_only floor 0.50 → review.',
    verdict: 'review',
    verdictNodeId: 'verdict-review',
    routingReason:
      "Meets helpfulness and coherence thresholds. Downgraded from 'pass': confidence 0.40 is below the read_only floor of 0.50.",
    confidence: 0.4,
    decidingAnswers: ['score.helpfulness', 'score.coherence'],
    mockAnswers: {
      'score.helpfulness': { type: 'score', score: 3.0, confidence: 0.4 },
      'score.coherence': { type: 'score', score: 3.0, confidence: 0.95 },
    },
    decidingPath: [
      'input',
      'state-filter',
      'jev-call',
      'score',
      'answers',
      'route',
      'confidence-floor',
      'verdict-review',
      'result',
    ],
    steps: [
      ...SETUP,
      JEV_BATCHED('Score stage supplies deciding answers; floor gates automatic pass.'),
      {
        nodeId: 'score',
        deciding: true,
        dwellMs: 950,
        stepNote: 'helpfulness 3.0 ≥ 2 · coherence 3.0 ≥ 2 — pass rule matches on scores.',
        showAnswers: ['score.helpfulness', 'score.coherence'],
      },
      ANSWERS,
      {
        ...ROUTE,
        stepNote: 'pass rule matched; confidence = min(0.40, 0.95) = 0.40.',
      },
      ...tail(
        'verdict-review',
        "Downgraded from 'pass': confidence 0.40 is below the read_only floor of 0.50.",
        true,
      ),
    ],
  },
];

export const SCENARIO_BY_ID = Object.fromEntries(PLAY_SCENARIOS.map((s) => [s.id, s])) as Record<
  string,
  PlayScenario
>;

export const DEFAULT_SCENARIO_ID = 'assistant-reply-pass';

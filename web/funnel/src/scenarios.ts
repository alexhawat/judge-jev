/** Play-through scenarios derived from repo fixtures + assistant-reply rubric routing. */

export interface MockAnswer {
  type: string;
  noul?: number;
  choice?: string;
  score?: number;
  confidence?: number;
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
  /** Ordered funnel node ids for step animation. */
  steps: string[];
}

const CORE_PREFIX = [
  'input',
  'state-filter',
  'jev-call',
  'screen',
  'profile',
  'locate',
  'score',
  'route-q',
  'answers',
  'route',
] as const;

function stepsToVerdict(verdictNodeId: string, viaFloor = true): string[] {
  const tail = viaFloor
    ? ['confidence-floor', verdictNodeId, 'result']
    : [verdictNodeId, 'result'];
  return [...CORE_PREFIX, ...tail];
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
    steps: stepsToVerdict('verdict-pass'),
  },
  {
    id: 'assistant-reply-fail',
    label: 'fail — terse refusal',
    fixtureFile: 'assistant-reply-fail.json',
    summary: 'Reply is a bare refusal with minimal helpfulness; matches fail rule from fixture _mock_answers.',
    verdict: 'fail',
    verdictNodeId: 'verdict-fail',
    routingReason: 'Unhelpful refusal without adequate explanation.',
    confidence: 0.88,
    decidingAnswers: ['profile.intent', 'score.helpfulness'],
    mockAnswers: {
      'profile.intent': { type: 'choice', choice: 'refusal', confidence: 0.88 },
      'score.helpfulness': { type: 'score', score: 0.4, confidence: 0.91 },
    },
    steps: stepsToVerdict('verdict-fail'),
  },
  {
    id: 'assistant-reply-escalate-flagged',
    label: 'escalate — route flag',
    fixtureFile: 'assistant-reply-escalate-flagged.json',
    summary: 'Medical-adjacent reply; route.escalate noul ≥ 0.7 triggers escalate (fixture _mock_answers).',
    verdict: 'escalate',
    verdictNodeId: 'verdict-escalate',
    routingReason: 'Model flagged this reply for human review.',
    confidence: 0.7,
    decidingAnswers: ['route.escalate'],
    mockAnswers: {
      'route.escalate': { type: 'noul', noul: 0.85 },
    },
    steps: stepsToVerdict('verdict-escalate'),
  },
  {
    id: 'assistant-reply-injection',
    label: 'escalate — prompt injection',
    fixtureFile: 'assistant-reply-injection.json',
    summary: 'Untrusted prompt tries to override judge; screen.injection ≥ 0.7 → escalate (simulated mock for demo).',
    verdict: 'escalate',
    verdictNodeId: 'verdict-escalate',
    routingReason: 'Possible prompt injection in untrusted state.',
    confidence: 0.76,
    decidingAnswers: ['screen.injection'],
    mockAnswers: {
      'screen.injection': { type: 'noul', noul: 0.82 },
    },
    steps: stepsToVerdict('verdict-escalate'),
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
    steps: stepsToVerdict('verdict-review'),
  },
];

export const SCENARIO_BY_ID = Object.fromEntries(PLAY_SCENARIOS.map((s) => [s.id, s])) as Record<
  string,
  PlayScenario
>;

export const DEFAULT_SCENARIO_ID = 'assistant-reply-pass';

import type { PlayStep } from './scenarios';

/** Default 8–12 word plain-English popup when a step omits `plainEnglish`. */
export const DEFAULT_PLAIN_ENGLISH: Record<string, string> = {
  input: "Load the reply JSON we're going to judge.",
  'state-filter': 'Keep only the fields this rubric allows.',
  'jev-call': 'Ask TypeSafe all rubric questions in one call.',
  screen: 'Check if the prompt tries to hijack the judge.',
  profile: 'Classify what kind of reply this is.',
  locate: 'Find where in the reply the answer lives.',
  score: 'Score helpfulness and coherence for this reply.',
  'route-q': 'Ask whether this reply needs human review.',
  answers: 'Collect normalized answers from the model response.',
  route: 'Apply the first matching rule to pick a verdict.',
  'confidence-floor': 'Downgrade weak automatic pass/fail to review.',
  'verdict-pass': 'Final decision: pass this reply.',
  'verdict-fail': 'Final decision: fail this reply.',
  'verdict-escalate': 'Final decision: escalate for human review.',
  'verdict-review': 'Final decision: send to human review.',
  result: 'Output the final JudgmentResult JSON verdict.',
};

export function plainEnglishForStep(step: PlayStep): string {
  return step.plainEnglish ?? DEFAULT_PLAIN_ENGLISH[step.nodeId] ?? 'Walk through this stage of the funnel.';
}

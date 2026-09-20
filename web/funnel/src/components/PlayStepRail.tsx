import { useEffect, useRef } from 'react';
import type { FunnelNodeMeta } from '../funnelData';
import type { PlayScenario, PlayStep } from '../scenarios';

type Props = {
  scenario: PlayScenario;
  currentStep: PlayStep | undefined;
  stepIndex: number;
  stepTotal: number;
  nodeMeta: FunnelNodeMeta | null;
};

function formatMockAnswer(key: string, answer: PlayScenario['mockAnswers'][string]) {
  if (answer.type === 'noul' && answer.noul != null) {
    return `noul ${answer.noul.toFixed(2)} (confidence ≈ ${(Math.abs(answer.noul - 0.5) * 2).toFixed(2)})`;
  }
  if (answer.type === 'choice') {
    return `${answer.choice} · confidence ${answer.confidence?.toFixed(2) ?? '—'}`;
  }
  if (answer.type === 'score') {
    return `score ${answer.score?.toFixed(1)} · confidence ${answer.confidence?.toFixed(2) ?? '—'}`;
  }
  return key;
}

export default function PlayStepRail({ scenario, currentStep, stepIndex, stepTotal, nodeMeta }: Props) {
  const cardRef = useRef<HTMLElement>(null);

  useEffect(() => {
    cardRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [stepIndex, currentStep?.nodeId]);

  const stepAnswers =
    currentStep?.showAnswers?.length
      ? currentStep.showAnswers
          .filter((k) => scenario.mockAnswers[k])
          .map((k) => [k, scenario.mockAnswers[k]] as const)
      : [];

  return (
    <aside className="play-rail" ref={cardRef} aria-live="polite">
      <div className="play-rail-inner">
        <p className="play-rail-label">Now playing</p>
        <h2 className="play-rail-stage">{nodeMeta?.title ?? currentStep?.nodeId ?? 'Step'}</h2>
        <p className="play-rail-fixture">{scenario.label}</p>
        <p className="play-rail-progress">
          Step {stepIndex + 1} of {stepTotal}
        </p>
        {currentStep ? (
          <p className="play-rail-note">{currentStep.stepNote}</p>
        ) : (
          <p className="play-rail-note">{scenario.summary}</p>
        )}
        {stepAnswers.length > 0 ? (
          <div className="play-answers-now">
            {stepAnswers.map(([k, v]) => (
              <div key={k} className="play-answer-row">
                <span className="play-answer-key">{k}</span>
                <span className="play-answer-val">{formatMockAnswer(k, v)}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className="play-verdict-chip" data-verdict={scenario.verdict}>
          target → {scenario.verdict}
          {scenario.confidence > 0 ? ` · confidence ${scenario.confidence.toFixed(2)}` : ''}
        </div>
        {scenario.decidingAnswers.length > 0 ? (
          <p className="play-deciding">deciding_answers: {scenario.decidingAnswers.join(', ')}</p>
        ) : null}
        {currentStep?.nodeId === scenario.verdictNodeId ? (
          <p className="play-routing-reason">{scenario.routingReason}</p>
        ) : null}
      </div>
    </aside>
  );
}

import type { FunnelNodeMeta } from '../funnelData';
import type { PlayScenario, PlayStep } from '../scenarios';

export type PanelContext = {
  mode: 'idle' | 'inspect' | 'play';
  scenario?: PlayScenario;
  currentStep?: PlayStep;
  stepIndex?: number;
  stepTotal?: number;
};

type Props = {
  node: FunnelNodeMeta | null;
  context: PanelContext;
  onClose: () => void;
};

function formatMockAnswer(key: string, answer: PlayScenario['mockAnswers'][string]) {
  if (answer.type === 'noul' && answer.noul != null) {
    return `${key}: noul ${answer.noul.toFixed(2)} (confidence ≈ ${(Math.abs(answer.noul - 0.5) * 2).toFixed(2)})`;
  }
  if (answer.type === 'choice') {
    return `${answer.choice} · confidence ${answer.confidence?.toFixed(2) ?? '—'}`;
  }
  if (answer.type === 'score') {
    return `score ${answer.score?.toFixed(1)} · confidence ${answer.confidence?.toFixed(2) ?? '—'}`;
  }
  return key;
}

export default function SidePanel({ node, context, onClose }: Props) {
  const { scenario, mode, currentStep, stepIndex, stepTotal } = context;

  const stepAnswers =
    mode === 'play' && scenario && currentStep?.showAnswers?.length
      ? currentStep.showAnswers
          .filter((k) => scenario.mockAnswers[k])
          .map((k) => [k, scenario.mockAnswers[k]] as const)
      : [];

  return (
    <aside className={`side-panel ${node || mode === 'play' ? 'is-open' : ''}`} aria-live="polite">
      <div className="side-panel-header">
        <h2>{node ? node.title : mode === 'play' ? 'Playing sample' : 'Explore the funnel'}</h2>
        {node ? (
          <button type="button" className="side-panel-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        ) : null}
      </div>

      {mode === 'play' && scenario ? (
        <div className="side-panel-play-banner">
          <div className="play-fixture-name">{scenario.label}</div>
          {stepIndex != null && stepTotal ? (
            <div className="play-step-counter">
              Step {stepIndex + 1} of {stepTotal}
            </div>
          ) : null}
          {currentStep ? (
            <p className="play-step-note">{currentStep.stepNote}</p>
          ) : (
            <p className="play-summary">{scenario.summary}</p>
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
      ) : null}

      {node ? (
        <div className="side-panel-body">
          <p className="side-panel-kind">{node.kind === 'jev' ? 'Jev API call' : node.kind}</p>
          <p>{node.body}</p>
          {node.bullets?.length ? (
            <ul>
              {node.bullets.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
          {node.codeRef ? <p className="side-panel-ref">Code: {node.codeRef}</p> : null}
        </div>
      ) : (
        <div className="side-panel-body side-panel-hint">
          <p>Hover or click nodes to highlight the path from Input. Edge labels show real assistant-reply routing snippets.</p>
          <p>Pick a fixture and press <strong>Play</strong> — each scenario follows its own deciding path.</p>
          <p>Press <kbd>Esc</kbd> to clear selection and path highlight.</p>
        </div>
      )}
    </aside>
  );
}

import type { FunnelNodeMeta } from '../funnelData';
import type { PlayScenario } from '../scenarios';

export type PanelContext = {
  mode: 'idle' | 'inspect' | 'play';
  scenario?: PlayScenario;
  stepIndex?: number;
  stepTotal?: number;
  playNote?: string;
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
    return `${key}: ${answer.choice} (confidence ${answer.confidence?.toFixed(2) ?? '—'})`;
  }
  if (answer.type === 'score') {
    return `${key}: score ${answer.score?.toFixed(1)} (confidence ${answer.confidence?.toFixed(2) ?? '—'})`;
  }
  return key;
}

export default function SidePanel({ node, context, onClose }: Props) {
  const { scenario, mode, playNote, stepIndex, stepTotal } = context;

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
          <p className="play-summary">{scenario.summary}</p>
          {playNote ? <p className="play-note">{playNote}</p> : null}
          <div className="play-verdict-chip" data-verdict={scenario.verdict}>
            → {scenario.verdict}
            {scenario.confidence > 0 ? ` · confidence ${scenario.confidence.toFixed(2)}` : ''}
          </div>
          {scenario.decidingAnswers.length > 0 ? (
            <p className="play-deciding">
              deciding: {scenario.decidingAnswers.join(', ')}
            </p>
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
          {mode === 'play' && scenario && relevantMocks(node, scenario).length > 0 ? (
            <div className="side-panel-mocks">
              <h3>Fixture answers at this stage</h3>
              <ul>
                {relevantMocks(node, scenario).map(([k, v]) => (
                  <li key={k}>
                    <code>{formatMockAnswer(k, v)}</code>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {node.codeRef ? <p className="side-panel-ref">Code: {node.codeRef}</p> : null}
        </div>
      ) : (
        <div className="side-panel-body side-panel-hint">
          <p>Hover or click nodes to highlight the path from Input. Edge labels show real assistant-reply routing snippets.</p>
          <p>Pick a fixture and press <strong>Play</strong> to animate a sample judgment end-to-end.</p>
          <p>Press <kbd>Esc</kbd> to clear selection and path highlight.</p>
        </div>
      )}
    </aside>
  );
}

const STAGE_ANSWERS: Record<string, string[]> = {
  screen: ['screen.judgeable', 'screen.injection'],
  profile: ['profile.intent'],
  locate: ['locate.hallucination_risk', 'locate.harmful'],
  score: ['score.helpfulness', 'score.coherence'],
  'route-q': ['route.escalate'],
  route: [],
  'confidence-floor': [],
};

function relevantMocks(node: FunnelNodeMeta, scenario: PlayScenario) {
  const keys = STAGE_ANSWERS[node.id] ?? Object.keys(scenario.mockAnswers);
  return Object.entries(scenario.mockAnswers).filter(([k]) =>
    node.id === 'answers' || node.id === 'route' || node.id === 'confidence-floor'
      ? true
      : keys.includes(k),
  );
}

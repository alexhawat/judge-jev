import { useEffect, useRef } from 'react';
import type { FunnelNodeMeta } from '../funnelData';
import type { PanelContext } from './SidePanel';
import InspectPanel from './InspectPanel';

type Props = {
  context: PanelContext;
  node: FunnelNodeMeta | null;
  playActive: boolean;
  sheetExpanded: boolean;
  inspectOpen: boolean;
  onToggleSheet: () => void;
  onCloseInspect: () => void;
};

function PlaySheetBody({ context, node }: { context: PanelContext; node: FunnelNodeMeta | null }) {
  const noteRef = useRef<HTMLParagraphElement>(null);
  const { scenario, mode, currentStep, stepIndex, stepTotal } = context;

  useEffect(() => {
    noteRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [stepIndex, currentStep?.nodeId]);

  if (mode === 'play' && scenario) {
    const stepAnswers =
      currentStep?.showAnswers?.length
        ? currentStep.showAnswers
            .filter((k) => scenario.mockAnswers[k])
            .map((k) => [k, scenario.mockAnswers[k]] as const)
        : [];

    return (
      <div className="side-panel-play-banner">
        <div className="play-fixture-name">{scenario.label}</div>
        {stepIndex != null && stepTotal ? (
          <div className="play-step-counter">
            Step {stepIndex + 1} of {stepTotal}
          </div>
        ) : null}
        {node ? <h3 className="mobile-step-title">{node.title}</h3> : null}
        {currentStep ? (
          <p ref={noteRef} className="play-step-note">
            {currentStep.stepNote}
          </p>
        ) : (
          <p className="play-summary">{scenario.summary}</p>
        )}
        {stepAnswers.length > 0 ? (
          <div className="play-answers-now">
            {stepAnswers.map(([k, v]) => (
              <div key={k} className="play-answer-row">
                <span className="play-answer-key">{k}</span>
                <span className="play-answer-val">{String(v.choice ?? v.score ?? v.noul)}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className="play-verdict-chip" data-verdict={scenario.verdict}>
          target → {scenario.verdict}
        </div>
      </div>
    );
  }

  if (node) {
    return (
      <>
        <p className="side-panel-kind">{node.kind === 'jev' ? 'Jev API call' : node.kind}</p>
        <p>{node.body}</p>
      </>
    );
  }

  return null;
}

export default function MobilePlaySheet({
  context,
  node,
  playActive,
  sheetExpanded,
  inspectOpen,
  onToggleSheet,
  onCloseInspect,
}: Props) {
  const hasContent = playActive || (inspectOpen && node);
  const expanded = playActive || sheetExpanded;
  const peekTitle =
    context.mode === 'play' && context.currentStep
      ? context.currentStep.stepNote
      : node?.title ?? 'Details';

  if (!hasContent) return null;

  return (
    <>
      {!expanded ? (
        <button type="button" className="sheet-peek" onClick={onToggleSheet} aria-expanded={false}>
          <span className="sheet-peek-label">{playActive ? 'Playing' : 'Details'}</span>
          <span className="sheet-peek-text">{peekTitle}</span>
          <span className="sheet-peek-chevron" aria-hidden>
            ▲
          </span>
        </button>
      ) : null}

      <aside
        className={[
          'side-panel',
          'side-panel--mobile',
          'is-open',
          playActive ? 'side-panel--play' : '',
          expanded ? 'side-panel--expanded' : 'side-panel--collapsed',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        <button type="button" className="sheet-handle" onClick={onToggleSheet} aria-label="Toggle details">
          <span className="sheet-handle-bar" />
        </button>
        {inspectOpen && node && !playActive ? (
          <InspectPanel node={node} open onClose={onCloseInspect} />
        ) : (
          <PlaySheetBody context={context} node={node} />
        )}
      </aside>
    </>
  );
}

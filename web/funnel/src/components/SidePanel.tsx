import type { FunnelNodeMeta } from '../funnelData';

type Props = {
  node: FunnelNodeMeta | null;
  onClose: () => void;
};

export default function SidePanel({ node, onClose }: Props) {
  return (
    <aside className={`side-panel ${node ? 'is-open' : ''}`} aria-live="polite">
      <div className="side-panel-header">
        <h2>{node ? node.title : 'Select a node'}</h2>
        {node ? (
          <button type="button" className="side-panel-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        ) : null}
      </div>
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
          <p>Click any node in the funnel to see what it does and how it maps to judge-jev code.</p>
          <p>Pan with drag, zoom with scroll or pinch, and select nodes to inspect constraints from the README and rubrics.</p>
        </div>
      )}
    </aside>
  );
}

import type { FunnelNodeMeta } from '../funnelData';

type Props = {
  node: FunnelNodeMeta;
  open: boolean;
  onClose: () => void;
};

export default function InspectPanel({ node, open, onClose }: Props) {
  if (!open) return null;

  return (
    <aside className="inspect-panel is-open" aria-live="polite">
      <div className="side-panel-header">
        <h2>{node.title}</h2>
        <button type="button" className="side-panel-close" onClick={onClose} aria-label="Close panel">
          ×
        </button>
      </div>
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
    </aside>
  );
}

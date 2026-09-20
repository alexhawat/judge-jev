import { memo } from 'react';
import { Handle, NodeToolbar, Position, type NodeProps } from '@xyflow/react';
import type { FunnelNodeMeta } from '../funnelData';

export type NodeVisualState = 'idle' | 'hover' | 'selected' | 'playing' | 'dimmed' | 'batched';

export type FunnelNodeData = {
  meta: FunnelNodeMeta;
  visualState?: NodeVisualState;
  /** Plain-English popup during Play / manual stepping / complete. */
  stepPopup?: string | null;
};

const kindClass: Record<FunnelNodeMeta['kind'], string> = {
  stage: 'node-stage',
  jev: 'node-jev',
  verdict: 'node-verdict',
};

function FunnelNodeComponent({ data, selected }: NodeProps & { data: FunnelNodeData }) {
  const { meta, visualState = 'idle', stepPopup } = data;
  const classes = [
    'funnel-node',
    kindClass[meta.kind],
    selected || visualState === 'selected' ? 'is-selected' : '',
    visualState === 'hover' ? 'is-hover' : '',
    visualState === 'playing' ? 'is-playing' : '',
    visualState === 'dimmed' ? 'is-dimmed' : '',
    visualState === 'batched' ? 'is-batched' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const tooltip = meta.subtitle ?? meta.label;
  const showStepPopup = Boolean(stepPopup);
  const showHoverTip = visualState === 'hover' && !showStepPopup;

  return (
    <>
      {showStepPopup ? (
        <NodeToolbar isVisible position={Position.Right} offset={16} align="center">
          <div className="node-step-popup node-step-popup--right" role="status" aria-live="polite">
            {stepPopup}
          </div>
        </NodeToolbar>
      ) : null}
      {showHoverTip ? (
        <NodeToolbar isVisible position={Position.Top} offset={8}>
          <div className="node-tooltip">{tooltip}</div>
        </NodeToolbar>
      ) : null}
      <div className={classes} title={tooltip}>
        <Handle type="target" position={Position.Top} className="funnel-handle" />
        <div className="funnel-node-label">{meta.label}</div>
        {meta.subtitle ? <div className="funnel-node-sub">{meta.subtitle}</div> : null}
        <Handle type="source" position={Position.Bottom} className="funnel-handle" />
      </div>
    </>
  );
}

export default memo(FunnelNodeComponent);

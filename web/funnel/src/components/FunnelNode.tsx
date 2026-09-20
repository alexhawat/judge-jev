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
      <NodeToolbar
        isVisible={showStepPopup || showHoverTip}
        position={Position.Top}
        offset={showStepPopup ? 14 : 8}
      >
        {showStepPopup ? (
          <div className="node-step-popup" role="status" aria-live="polite">
            {stepPopup}
          </div>
        ) : (
          <div className="node-tooltip">{tooltip}</div>
        )}
      </NodeToolbar>
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

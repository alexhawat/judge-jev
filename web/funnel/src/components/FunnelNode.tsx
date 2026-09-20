import { memo } from 'react';
import { Handle, NodeToolbar, Position, type NodeProps } from '@xyflow/react';
import type { FunnelNodeMeta } from '../funnelData';

export type NodeVisualState = 'idle' | 'hover' | 'selected' | 'playing' | 'dimmed' | 'batched';

export type FunnelNodeData = {
  meta: FunnelNodeMeta;
  visualState?: NodeVisualState;
};

const kindClass: Record<FunnelNodeMeta['kind'], string> = {
  stage: 'node-stage',
  jev: 'node-jev',
  verdict: 'node-verdict',
};

function FunnelNodeComponent({ data, selected }: NodeProps & { data: FunnelNodeData }) {
  const { meta, visualState = 'idle' } = data;
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

  return (
    <>
      <NodeToolbar isVisible={visualState === 'hover' || visualState === 'playing'} position={Position.Top}>
        <div className="node-tooltip">{tooltip}</div>
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

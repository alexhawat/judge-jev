import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { FunnelNodeMeta } from '../funnelData';

export type FunnelNodeData = {
  meta: FunnelNodeMeta;
  selected?: boolean;
};

const kindClass: Record<FunnelNodeMeta['kind'], string> = {
  stage: 'node-stage',
  jev: 'node-jev',
  verdict: 'node-verdict',
};

function FunnelNodeComponent({ data, selected }: NodeProps & { data: FunnelNodeData }) {
  const { meta } = data;
  const classes = ['funnel-node', kindClass[meta.kind], selected ? 'is-selected' : '']
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      <Handle type="target" position={Position.Left} className="funnel-handle" />
      <div className="funnel-node-label">{meta.label}</div>
      {meta.subtitle ? <div className="funnel-node-sub">{meta.subtitle}</div> : null}
      <Handle type="source" position={Position.Right} className="funnel-handle" />
    </div>
  );
}

export default memo(FunnelNodeComponent);

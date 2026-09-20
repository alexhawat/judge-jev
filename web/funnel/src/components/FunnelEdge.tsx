import { memo } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react';

export type FunnelEdgeData = {
  dimmed?: boolean;
  active?: boolean;
  hideLabel?: boolean;
};

function FunnelEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  label,
  data,
  markerEnd,
}: EdgeProps & { data?: FunnelEdgeData }) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  const dimmed = data?.dimmed ?? false;
  const active = data?.active ?? false;
  const showLabel = label && !data?.hideLabel;

  return (
    <>
      {active ? (
        <BaseEdge
          id={`${id}-glow`}
          path={edgePath}
          markerEnd={markerEnd}
          style={{
            stroke: '#7c5cff',
            strokeWidth: 8,
            opacity: 0.35,
            filter: 'blur(3px)',
          }}
          interactionWidth={0}
        />
      ) : null}
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        className={`funnel-edge ${dimmed ? 'is-dimmed' : ''} ${active ? 'is-active' : ''}`}
        style={{
          stroke: active ? '#a78bfa' : dimmed ? '#2a3344' : '#5a6a82',
          strokeWidth: active ? 2.5 : 1.5,
        }}
      />
      {showLabel ? (
        <EdgeLabelRenderer>
          <div
            className={`edge-label ${dimmed ? 'is-dimmed' : ''} ${active ? 'is-active' : ''}`}
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              pointerEvents: 'none',
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export default memo(FunnelEdgeComponent);

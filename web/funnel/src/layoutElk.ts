import ELK from 'elkjs/lib/elk.bundled.js';
import type { Edge, Node } from '@xyflow/react';
import { FUNNEL_EDGES, FUNNEL_NODES, type FunnelNodeMeta } from './funnelData';
import type { FunnelNodeData } from './components/FunnelNode';

const elk = new ELK();

const NODE_WIDTH = 248;
const NODE_HEIGHT = 88;
const JEV_WIDTH = 272;

export async function layoutFunnel(): Promise<{ nodes: Node<FunnelNodeData>[]; edges: Edge[] }> {
  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.spacing.nodeNode': '28',
      'elk.layered.spacing.nodeNodeBetweenLayers': '44',
      'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
    },
    children: FUNNEL_NODES.map((meta) => ({
      id: meta.id,
      width: meta.kind === 'jev' ? JEV_WIDTH : NODE_WIDTH,
      height: meta.subtitle ? NODE_HEIGHT + 16 : NODE_HEIGHT,
    })),
    edges: FUNNEL_EDGES.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const laidOut = await elk.layout(graph);

  const posById = new Map(
    (laidOut.children ?? []).map((c) => [c.id, { x: c.x ?? 0, y: c.y ?? 0 }]),
  );

  const nodes: Node<FunnelNodeData>[] = FUNNEL_NODES.map((meta: FunnelNodeMeta) => {
    const pos = posById.get(meta.id) ?? { x: 0, y: 0 };
    return {
      id: meta.id,
      type: 'funnel',
      position: pos,
      data: { meta, visualState: 'idle' },
    };
  });

  const edges: Edge[] = FUNNEL_EDGES.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: 'funnel',
    label: e.label,
    data: { dimmed: false, active: false, hideLabel: false },
  }));

  return { nodes, edges };
}

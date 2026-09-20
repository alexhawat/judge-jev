import { useCallback, useEffect, useState } from 'react';
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type OnSelectionChangeParams,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import FunnelNode, { type FunnelNodeData } from './components/FunnelNode';
import SidePanel from './components/SidePanel';
import { NODE_BY_ID } from './funnelData';
import { layoutFunnel } from './layoutElk';

const nodeTypes = { funnel: FunnelNode };

export default function App() {
  const [nodes, setNodes] = useState<Node<FunnelNodeData>[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    layoutFunnel().then(({ nodes: n, edges: e }) => {
      setNodes(n);
      setEdges(e);
      setReady(true);
    });
  }, []);

  const onSelectionChange = useCallback(({ nodes: selected }: OnSelectionChangeParams) => {
    setSelectedId(selected.length === 1 ? selected[0].id : null);
  }, []);

  const selectedMeta = selectedId ? NODE_BY_ID[selectedId] ?? null : null;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>judge-jev judgment funnel</h1>
          <p className="app-tagline">
            screen → profile → locate → score → route — one TypeSafe <code>system_one</code> call,
            declarative YAML routing, confidence floors
          </p>
        </div>
        <div className="legend">
          <span className="legend-item legend-stage">Stage</span>
          <span className="legend-item legend-jev">Jev call</span>
          <span className="legend-item legend-verdict">Verdict</span>
        </div>
      </header>
      <main className="app-main">
        <div className="flow-wrap">
          {ready ? (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onSelectionChange={onSelectionChange}
              fitView
              fitViewOptions={{ padding: 0.18 }}
              minZoom={0.25}
              maxZoom={1.6}
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} color="#2a3344" />
              <Controls showInteractive={false} />
              <MiniMap
                nodeColor={(n) => {
                  const kind = (n.data as FunnelNodeData)?.meta?.kind;
                  if (kind === 'jev') return '#7c5cff';
                  if (kind === 'verdict') return '#3ecf8e';
                  return '#4a90d9';
                }}
                maskColor="rgba(10, 14, 22, 0.75)"
              />
            </ReactFlow>
          ) : (
            <div className="flow-loading">Laying out funnel…</div>
          )}
        </div>
        <SidePanel node={selectedMeta} onClose={() => setSelectedId(null)} />
      </main>
    </div>
  );
}

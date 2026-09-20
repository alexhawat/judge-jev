import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type OnMove,
  type OnSelectionChangeParams,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import FitViewHelper from './components/FitViewHelper';
import FunnelNode, { type FunnelNodeData, type NodeVisualState } from './components/FunnelNode';
import FunnelEdge from './components/FunnelEdge';
import SidePanel, { type PanelContext } from './components/SidePanel';
import Toolbar from './components/Toolbar';
import { FUNNEL_EDGES, NODE_BY_ID } from './funnelData';
import { computeHighlight, computePlayHighlight } from './pathUtils';
import { DEFAULT_SCENARIO_ID, SCENARIO_BY_ID, type PlayScenario } from './scenarios';
import { layoutFunnel } from './layoutElk';

const nodeTypes = { funnel: FunnelNode };
const edgeTypes = { funnel: FunnelEdge };

const LABEL_ZOOM_MIN = 0.5;

function readNodeFromUrl(): string | null {
  const id = new URLSearchParams(window.location.search).get('node');
  return id && NODE_BY_ID[id] ? id : null;
}

function playPathIndex(scenario: PlayScenario, stepIndex: number): number {
  const visited = scenario.steps.slice(0, stepIndex + 1).map((s) => s.nodeId);
  let maxIdx = 0;
  for (const id of visited) {
    const idx = scenario.decidingPath.indexOf(id);
    if (idx > maxIdx) maxIdx = idx;
  }
  return maxIdx;
}

function FunnelDiagram({
  baseNodes,
  baseEdges,
  focusId,
  hoverId,
  selectedId,
  playing,
  playHighlight,
  hideLabels,
  onHover,
  onSelectionChange,
  onZoom,
  ready,
}: {
  baseNodes: Node<FunnelNodeData>[];
  baseEdges: Edge[];
  focusId: string | null;
  hoverId: string | null;
  selectedId: string | null;
  playing: boolean;
  playHighlight: ReturnType<typeof computePlayHighlight> | null;
  hideLabels: boolean;
  onHover: (id: string | null) => void;
  onSelectionChange: (params: OnSelectionChangeParams) => void;
  onZoom: (zoom: number) => void;
  ready: boolean;
}) {
  const highlight = playHighlight ?? computeHighlight(focusId, FUNNEL_EDGES);
  const hasFocus = Boolean(focusId || playHighlight);

  const nodes = useMemo(() => {
    return baseNodes.map((n) => {
      let visualState: NodeVisualState = 'idle';
      if (focusId === n.id && playing) visualState = 'playing';
      else if (hoverId === n.id && hoverId !== selectedId) visualState = 'hover';
      else if (selectedId === n.id || focusId === n.id) visualState = 'selected';
      else if (hasFocus && !highlight.nodes.has(n.id)) visualState = 'dimmed';
      return { ...n, data: { ...n.data, visualState } };
    });
  }, [baseNodes, focusId, hoverId, selectedId, playing, hasFocus, highlight.nodes]);

  const edges = useMemo(() => {
    return baseEdges.map((e) => {
      const active = highlight.edgeIds.has(e.id);
      const dimmed = hasFocus && !active;
      return {
        ...e,
        animated: active,
        data: { ...e.data, active, dimmed, hideLabel: hideLabels },
      };
    });
  }, [baseEdges, highlight.edgeIds, hasFocus, hideLabels]);

  const onMove: OnMove = useCallback(
    (_evt, viewport) => {
      onZoom(viewport.zoom);
    },
    [onZoom],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onSelectionChange={onSelectionChange}
      onNodeMouseEnter={(_, node) => onHover(node.id)}
      onNodeMouseLeave={() => onHover(null)}
      onPaneClick={() => onHover(null)}
      onMove={onMove}
      minZoom={0.55}
      maxZoom={1.6}
      proOptions={{ hideAttribution: true }}
    >
      <FitViewHelper ready={ready} />
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
  );
}

function AppInner() {
  const [baseNodes, setBaseNodes] = useState<Node<FunnelNodeData>[]>([]);
  const [baseEdges, setBaseEdges] = useState<Edge[]>([]);
  const [ready, setReady] = useState(false);
  const [zoom, setZoom] = useState(1);

  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(() => readNodeFromUrl());

  const [scenarioId, setScenarioId] = useState(DEFAULT_SCENARIO_ID);
  const [playing, setPlaying] = useState(false);
  const [paused, setPaused] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);

  const timerRef = useRef<number | null>(null);
  const scenario = SCENARIO_BY_ID[scenarioId];
  const hideLabels = zoom < LABEL_ZOOM_MIN;
  const currentStep = scenario?.steps[stepIndex];

  useEffect(() => {
    layoutFunnel().then(({ nodes, edges }) => {
      setBaseNodes(nodes);
      setBaseEdges(edges);
      setReady(true);
    });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (selectedId) params.set('node', selectedId);
    else params.delete('node');
    const qs = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`);
  }, [selectedId]);

  const clearPlayTimer = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const resetPlay = useCallback(() => {
    clearPlayTimer();
    setPlaying(false);
    setPaused(false);
    setStepIndex(0);
  }, [clearPlayTimer]);

  const focusId =
    playing || paused ? currentStep?.nodeId ?? null : hoverId ?? selectedId;

  const playHighlight = useMemo(() => {
    if (!scenario || (!playing && !paused)) return null;
    return computePlayHighlight(scenario.decidingPath, playPathIndex(scenario, stepIndex));
  }, [scenario, playing, paused, stepIndex]);

  const panelContext: PanelContext = useMemo(() => {
    if (playing || paused) {
      return {
        mode: 'play',
        scenario,
        currentStep,
        stepIndex,
        stepTotal: scenario?.steps.length,
      };
    }
    if (selectedId || hoverId) return { mode: 'inspect' };
    return { mode: 'idle' };
  }, [playing, paused, scenario, currentStep, stepIndex, selectedId, hoverId]);

  const panelNode = focusId ? NODE_BY_ID[focusId] ?? null : null;

  const advanceStep = useCallback(() => {
    setStepIndex((prev) => {
      const steps = SCENARIO_BY_ID[scenarioId]?.steps ?? [];
      if (prev >= steps.length - 1) {
        setPlaying(false);
        setPaused(false);
        return prev;
      }
      return prev + 1;
    });
  }, [scenarioId]);

  const startPlay = useCallback(() => {
    setStepIndex(0);
    setPlaying(true);
    setPaused(false);
    setSelectedId(scenario?.steps[0]?.nodeId ?? null);
  }, [scenario]);

  useEffect(() => {
    if (!playing || paused || !currentStep) {
      clearPlayTimer();
      return;
    }
    timerRef.current = window.setTimeout(advanceStep, currentStep.dwellMs);
    return clearPlayTimer;
  }, [playing, paused, stepIndex, currentStep, advanceStep, clearPlayTimer]);

  useEffect(() => {
    if ((playing || paused) && currentStep) {
      setSelectedId(currentStep.nodeId);
    }
  }, [stepIndex, playing, paused, currentStep]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setHoverId(null);
        setSelectedId(null);
        resetPlay();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [resetPlay]);

  const onSelectionChange = useCallback(
    ({ nodes }: OnSelectionChangeParams) => {
      if (playing && !paused) return;
      setSelectedId(nodes.length === 1 ? nodes[0].id : null);
    },
    [playing, paused],
  );

  const onScenarioChange = (id: string) => {
    resetPlay();
    setScenarioId(id);
    setSelectedId(null);
  };

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

      <Toolbar
        scenarioId={scenarioId}
        onScenarioChange={onScenarioChange}
        playing={playing}
        paused={paused}
        canPlay={ready}
        onPlay={() => (paused ? setPaused(false) : startPlay())}
        onPause={() => setPaused(true)}
        onReset={() => {
          resetPlay();
          setSelectedId(null);
        }}
        stepIndex={stepIndex}
        stepCount={scenario?.steps.length ?? 0}
      />

      <main className="app-main">
        <div className="flow-wrap">
          {ready ? (
            <FunnelDiagram
              baseNodes={baseNodes}
              baseEdges={baseEdges}
              focusId={focusId}
              hoverId={hoverId}
              selectedId={selectedId}
              playing={playing && !paused}
              playHighlight={playHighlight}
              hideLabels={hideLabels}
              ready={ready}
              onHover={(id) => {
                if (!playing || paused) setHoverId(id);
              }}
              onSelectionChange={onSelectionChange}
              onZoom={setZoom}
            />
          ) : (
            <div className="flow-loading">Laying out funnel…</div>
          )}
        </div>
        <SidePanel
          node={panelNode}
          context={panelContext}
          onClose={() => {
            setSelectedId(null);
            resetPlay();
          }}
        />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <AppInner />
    </ReactFlowProvider>
  );
}

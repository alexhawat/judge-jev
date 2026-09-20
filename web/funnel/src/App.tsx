import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  Controls,
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
import PlayFocusHelper from './components/PlayFocusHelper';
import SidePanel, { type PanelContext } from './components/SidePanel';
import Toolbar from './components/Toolbar';
import { FUNNEL_EDGES, NODE_BY_ID } from './funnelData';
import { useMobileLayout } from './hooks/useMediaQuery';
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
  playComplete,
  playHighlight,
  hideLabels,
  mobile,
  playFocusActive,
  selectionLocked,
  onHover,
  onNodeTap,
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
  playComplete: boolean;
  playHighlight: ReturnType<typeof computePlayHighlight> | null;
  hideLabels: boolean;
  mobile: boolean;
  playFocusActive: boolean;
  selectionLocked: boolean;
  onHover: (id: string | null) => void;
  onNodeTap: (id: string) => void;
  onSelectionChange: (params: OnSelectionChangeParams) => void;
  onZoom: (zoom: number) => void;
  ready: boolean;
}) {
  const playMode = Boolean(playHighlight);
  const highlight = playHighlight ?? computeHighlight(focusId, FUNNEL_EDGES);
  const hasFocus = Boolean(focusId || playHighlight);

  const nodes = useMemo(() => {
    return baseNodes.map((n) => {
      let visualState: NodeVisualState = 'idle';
      if (focusId === n.id && playing) visualState = 'playing';
      else if (focusId === n.id && playComplete) visualState = 'selected';
      else if (hoverId === n.id && hoverId !== selectedId) visualState = 'hover';
      else if (selectedId === n.id || focusId === n.id) visualState = 'selected';
      else if (hasFocus && !highlight.nodes.has(n.id)) visualState = 'dimmed';
      const selected = selectionLocked ? n.id === focusId : selectedId === n.id;
      return { ...n, selected, data: { ...n.data, visualState } };
    });
  }, [
    baseNodes,
    focusId,
    hoverId,
    selectedId,
    playing,
    playComplete,
    hasFocus,
    highlight.nodes,
    selectionLocked,
  ]);

  const edges = useMemo(() => {
    return baseEdges.map((e) => {
      const active = highlight.edgeIds.has(e.id);
      const dimmed = hasFocus && !active;
      const hideEdgeLabel = hideLabels || (playMode && Boolean(e.label) && !active);
      return {
        ...e,
        animated: active,
        data: { ...e.data, active, dimmed, hideLabel: hideEdgeLabel },
      };
    });
  }, [baseEdges, highlight.edgeIds, hasFocus, hideLabels, playMode]);

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
      onSelectionChange={selectionLocked ? undefined : onSelectionChange}
      onNodeMouseEnter={(_, node) => {
        if (!mobile && !playComplete) onHover(node.id);
      }}
      onNodeMouseLeave={() => {
        if (!mobile && !playComplete) onHover(null);
      }}
      onNodeClick={(_, node) => onNodeTap(node.id)}
      onPaneClick={() => {
        if (!playComplete) onHover(null);
      }}
      onMove={onMove}
      nodesFocusable={!selectionLocked}
      elementsSelectable={!selectionLocked}
      minZoom={0.55}
      maxZoom={1.6}
      panOnScroll={false}
      zoomOnPinch
      proOptions={{ hideAttribution: true }}
    >
      <FitViewHelper ready={ready} />
      {playFocusActive && focusId ? <PlayFocusHelper nodeId={focusId} active /> : null}
      <Background gap={20} color="#2a3344" />
      <Controls showInteractive={false} className="flow-controls" />
    </ReactFlow>
  );
}

function AppInner() {
  const mobile = useMobileLayout();
  const [baseNodes, setBaseNodes] = useState<Node<FunnelNodeData>[]>([]);
  const [baseEdges, setBaseEdges] = useState<Edge[]>([]);
  const [ready, setReady] = useState(false);
  const [zoom, setZoom] = useState(1);

  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(() => readNodeFromUrl());

  const [scenarioId, setScenarioId] = useState(DEFAULT_SCENARIO_ID);
  const [playing, setPlaying] = useState(false);
  const [paused, setPaused] = useState(false);
  const [playComplete, setPlayComplete] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [sheetExpanded, setSheetExpanded] = useState(false);

  const timerRef = useRef<number | null>(null);
  /** Sync guard — RF onSelectionChange can fire before playComplete state commits. */
  const playCompleteRef = useRef(false);
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
    playCompleteRef.current = false;
    setPlaying(false);
    setPaused(false);
    setPlayComplete(false);
    setStepIndex(0);
    if (mobile) setSheetExpanded(false);
  }, [clearPlayTimer, mobile]);

  const focusId =
    playing || paused
      ? currentStep?.nodeId ?? null
      : playComplete
        ? scenario?.verdictNodeId ?? null
        : hoverId ?? selectedId;

  const playHighlight = useMemo(() => {
    if (!scenario) return null;
    if (playComplete || playCompleteRef.current) {
      return computePlayHighlight(scenario.decidingPath, scenario.decidingPath.length - 1);
    }
    if (playing || paused) {
      return computePlayHighlight(scenario.decidingPath, playPathIndex(scenario, stepIndex));
    }
    return null;
  }, [scenario, playing, paused, playComplete, stepIndex]);

  const selectionLocked = playing || paused || playComplete || playCompleteRef.current;

  const panelContext: PanelContext = useMemo(() => {
    if (playing || paused || playComplete) {
      const lastStep = scenario?.steps[scenario.steps.length - 1];
      return {
        mode: 'play',
        scenario,
        currentStep: playComplete ? lastStep : currentStep,
        stepIndex: playComplete ? (scenario?.steps.length ?? 1) - 1 : stepIndex,
        stepTotal: scenario?.steps.length,
      };
    }
    if (selectedId || hoverId) return { mode: 'inspect' };
    return { mode: 'idle' };
  }, [playing, paused, playComplete, scenario, currentStep, stepIndex, selectedId, hoverId]);

  const panelNode = focusId ? NODE_BY_ID[focusId] ?? null : null;

  const advanceStep = useCallback(() => {
    setStepIndex((prev) => {
      const steps = SCENARIO_BY_ID[scenarioId]?.steps ?? [];
      if (prev >= steps.length - 1) {
        const s = SCENARIO_BY_ID[scenarioId];
        playCompleteRef.current = true;
        setPlaying(false);
        setPaused(false);
        setPlayComplete(true);
        if (s) setSelectedId(s.verdictNodeId);
        return prev;
      }
      return prev + 1;
    });
  }, [scenarioId]);

  const startPlay = useCallback(() => {
    playCompleteRef.current = false;
    setStepIndex(0);
    setPlaying(true);
    setPaused(false);
    setPlayComplete(false);
    setSelectedId(scenario?.steps[0]?.nodeId ?? null);
    if (mobile) setSheetExpanded(false);
  }, [scenario, mobile]);

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
      if (playCompleteRef.current || playComplete) return;
      const id = nodes.length === 1 ? nodes[0].id : null;
      setSelectedId(id);
      if (mobile && id) setSheetExpanded(true);
    },
    [playing, paused, playComplete, mobile],
  );

  const onNodeTap = useCallback(
    (id: string) => {
      if (playing && !paused) return;
      if (playCompleteRef.current || playComplete) {
        if (id !== scenario?.verdictNodeId) {
          playCompleteRef.current = false;
          setPlayComplete(false);
          setSelectedId(id);
          if (mobile) setSheetExpanded(true);
        }
        return;
      }
      setSelectedId(id);
      if (mobile) setSheetExpanded(true);
    },
    [playing, paused, playComplete, scenario, mobile],
  );

  const onScenarioChange = (id: string) => {
    resetPlay();
    setScenarioId(id);
    setSelectedId(null);
  };

  const playFocusActive = mobile && (playing || playComplete) && !paused;

  return (
    <div className={`app-shell ${mobile ? 'app-shell--mobile' : ''}`}>
      <header className="app-header">
        <div className="app-header-main">
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
        mobile={mobile}
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
              playComplete={playComplete}
              playHighlight={playHighlight}
              hideLabels={hideLabels}
              mobile={mobile}
              playFocusActive={playFocusActive}
              selectionLocked={selectionLocked}
              ready={ready}
              onHover={setHoverId}
              onNodeTap={onNodeTap}
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
          mobile={mobile}
          sheetExpanded={sheetExpanded}
          onToggleSheet={() => setSheetExpanded((v) => !v)}
          onClose={() => {
            setSelectedId(null);
            setSheetExpanded(false);
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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type OnMove,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import FitViewHelper from './components/FitViewHelper';
import FunnelErrorBoundary from './components/FunnelErrorBoundary';
import FunnelNode, { type FunnelNodeData, type NodeVisualState } from './components/FunnelNode';
import FunnelEdge from './components/FunnelEdge';
import InspectPanel from './components/InspectPanel';
import MobilePlaySheet from './components/MobilePlaySheet';
import PlayFocusHelper from './components/PlayFocusHelper';
import PlayStepRail from './components/PlayStepRail';
import Toolbar from './components/Toolbar';
import { FUNNEL_EDGES, NODE_BY_ID } from './funnelData';
import { useMobileLayout } from './hooks/useMediaQuery';
import { isPlayActive, isPlayInProgress, type PlayPhase } from './playPhase';
import { computeHighlight, computePlayHighlight } from './pathUtils';
import { DEFAULT_SCENARIO_ID, SCENARIO_BY_ID, type PlayScenario } from './scenarios';
import { plainEnglishForStep } from './stepPlainEnglish';
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
  phase,
  stepPopupText,
  playHighlight,
  hideLabels,
  mobile,
  playFocusActive,
  selectionLocked,
  resetToken,
  ready,
  onHover,
  onNodeTap,
  onClearSelection,
  onZoom,
}: {
  baseNodes: Node<FunnelNodeData>[];
  baseEdges: Edge[];
  focusId: string | null;
  hoverId: string | null;
  selectedId: string | null;
  phase: PlayPhase;
  stepPopupText: string | null;
  playHighlight: ReturnType<typeof computePlayHighlight> | null;
  hideLabels: boolean;
  mobile: boolean;
  playFocusActive: boolean;
  selectionLocked: boolean;
  resetToken: number;
  ready: boolean;
  onHover: (id: string | null) => void;
  onNodeTap: (id: string) => void;
  onClearSelection: () => void;
  onZoom: (zoom: number) => void;
}) {
  const playComplete = phase === 'complete';
  const stepTourActive = phase === 'playing' || phase === 'stepping';
  const playMode = Boolean(playHighlight);
  const highlight = playHighlight ?? (focusId ? computeHighlight(focusId, FUNNEL_EDGES) : null);
  const hasFocus = Boolean(highlight && (focusId || playHighlight));

  const nodes = useMemo(() => {
    return baseNodes.map((n) => {
      let visualState: NodeVisualState = 'idle';
      if (focusId === n.id && stepTourActive) visualState = 'playing';
      else if (focusId === n.id && playComplete) visualState = 'selected';
      else if (hoverId === n.id && hoverId !== selectedId) visualState = 'hover';
      else if (selectedId === n.id || focusId === n.id) visualState = 'selected';
      else if (hasFocus && highlight && !highlight.nodes.has(n.id)) visualState = 'dimmed';
      const selected = selectionLocked ? n.id === focusId : selectedId === n.id;
      const stepPopup = focusId === n.id ? stepPopupText : null;
      return { ...n, selected, data: { ...n.data, visualState, stepPopup } };
    });
  }, [
    baseNodes,
    focusId,
    hoverId,
    selectedId,
    stepTourActive,
    playComplete,
    hasFocus,
    highlight,
    selectionLocked,
    stepPopupText,
  ]);

  const edges = useMemo(() => {
    return baseEdges.map((e) => {
      const active = highlight?.edgeIds.has(e.id) ?? false;
      const dimmed = hasFocus && !active;
      const hideEdgeLabel = hideLabels || (playMode && Boolean(e.label) && !active);
      return {
        ...e,
        animated: active,
        data: { ...e.data, active, dimmed, hideLabel: hideEdgeLabel },
      };
    });
  }, [baseEdges, highlight, hasFocus, hideLabels, playMode]);

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
      onNodeMouseEnter={(_, node) => {
        if (!mobile && !playComplete && phase === 'idle') onHover(node.id);
      }}
      onNodeMouseLeave={() => {
        if (!mobile && !playComplete && phase === 'idle') onHover(null);
      }}
      onNodeClick={(_, node) => onNodeTap(node.id)}
      onPaneClick={() => {
        if (phase === 'idle') {
          onHover(null);
          onClearSelection();
        }
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
      <FitViewHelper ready={ready} resetToken={resetToken} />
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
  const [resetToken, setResetToken] = useState(0);

  const [hoverId, setHoverId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(() => readNodeFromUrl());
  const [inspectOpen, setInspectOpen] = useState(false);

  const [scenarioId, setScenarioId] = useState(DEFAULT_SCENARIO_ID);
  const [phase, setPhase] = useState<PlayPhase>('idle');
  const [playGen, setPlayGen] = useState(0);
  const [stepIndex, setStepIndex] = useState(0);
  const [sheetExpanded, setSheetExpanded] = useState(false);

  const timerRef = useRef<number | null>(null);
  const playGenRef = useRef(0);
  const stepIndexRef = useRef(0);

  const scenario = SCENARIO_BY_ID[scenarioId];
  const hideLabels = zoom < LABEL_ZOOM_MIN;
  const currentStep = scenario?.steps[stepIndex];
  const playInProgress = isPlayInProgress(phase);
  const playActive = isPlayActive(phase);
  const playComplete = phase === 'complete';
  const stepCount = scenario?.steps.length ?? 0;

  useEffect(() => {
    playGenRef.current = playGen;
  }, [playGen]);

  useEffect(() => {
    stepIndexRef.current = stepIndex;
  }, [stepIndex]);

  useEffect(() => {
    layoutFunnel().then(({ nodes, edges }) => {
      setBaseNodes(nodes);
      setBaseEdges(edges);
      setReady(true);
    });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (selectedId && !playActive) params.set('node', selectedId);
    else params.delete('node');
    const qs = params.toString();
    window.history.replaceState(null, '', `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`);
  }, [selectedId, playActive]);

  const clearPlayTimer = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const bumpPlayGen = useCallback(() => {
    setPlayGen((g) => g + 1);
  }, []);

  const resetPlay = useCallback(() => {
    bumpPlayGen();
    clearPlayTimer();
    setPhase('idle');
    setStepIndex(0);
    setHoverId(null);
    setSelectedId(null);
    setInspectOpen(false);
    setSheetExpanded(false);
    setResetToken((t) => t + 1);
  }, [bumpPlayGen, clearPlayTimer]);

  const onScenarioChange = useCallback(
    (id: string) => {
      if (!SCENARIO_BY_ID[id]) return;
      bumpPlayGen();
      clearPlayTimer();
      setPhase('idle');
      setStepIndex(0);
      setHoverId(null);
      setSelectedId(null);
      setInspectOpen(false);
      setSheetExpanded(false);
      setScenarioId(id);
      setResetToken((t) => t + 1);
    },
    [bumpPlayGen, clearPlayTimer],
  );

  const focusId = playActive
    ? playComplete
      ? scenario?.verdictNodeId ?? null
      : currentStep?.nodeId ?? null
    : hoverId ?? selectedId;

  const playHighlight = useMemo(() => {
    if (!scenario || phase === 'idle') return null;
    if (phase === 'complete') {
      return computePlayHighlight(scenario.decidingPath, scenario.decidingPath.length - 1);
    }
    return computePlayHighlight(scenario.decidingPath, playPathIndex(scenario, stepIndex));
  }, [scenario, phase, stepIndex]);

  const selectionLocked = playActive;

  const displayStep = playComplete ? scenario?.steps[scenario.steps.length - 1] : currentStep;
  const displayStepIndex = playComplete ? (scenario?.steps.length ?? 1) - 1 : stepIndex;
  const focusMeta = focusId ? NODE_BY_ID[focusId] ?? null : null;
  const inspectNode = selectedId ? NODE_BY_ID[selectedId] ?? null : null;

  const stepPopupText = useMemo(() => {
    if (!playActive || !scenario) return null;
    if (playComplete) {
      const verdictStep = scenario.steps.find((s) => s.nodeId === scenario.verdictNodeId);
      return verdictStep ? plainEnglishForStep(verdictStep) : null;
    }
    return currentStep ? plainEnglishForStep(currentStep) : null;
  }, [playActive, scenario, playComplete, currentStep]);

  const goToStep = useCallback(
    (idx: number, nextPhase: PlayPhase) => {
      const steps = scenario?.steps ?? [];
      if (idx < 0 || idx >= steps.length) return;
      clearPlayTimer();
      bumpPlayGen();
      setPhase(nextPhase);
      setStepIndex(idx);
      setSelectedId(steps[idx].nodeId);
      setInspectOpen(false);
      if (mobile) setSheetExpanded(true);
    },
    [scenario, clearPlayTimer, bumpPlayGen, mobile],
  );

  const finishTour = useCallback(() => {
    clearPlayTimer();
    bumpPlayGen();
    setPhase('complete');
    setInspectOpen(false);
    if (scenario) setSelectedId(scenario.verdictNodeId);
    if (mobile) setSheetExpanded(false);
  }, [scenario, clearPlayTimer, bumpPlayGen, mobile]);

  const advanceStep = useCallback(() => {
    const gen = playGenRef.current;
    const idx = stepIndexRef.current;
    const steps = scenario?.steps ?? [];
    if (idx >= steps.length - 1) {
      if (gen !== playGenRef.current) return;
      finishTour();
      return;
    }
    if (gen !== playGenRef.current) return;
    setStepIndex(idx + 1);
    setSelectedId(steps[idx + 1]?.nodeId ?? null);
  }, [scenario, finishTour]);

  const goNext = useCallback(() => {
    if (phase === 'complete') return;
    if (phase === 'idle') {
      goToStep(0, 'stepping');
      return;
    }
    const idx = stepIndexRef.current;
    const steps = scenario?.steps ?? [];
    if (idx >= steps.length - 1) {
      finishTour();
      return;
    }
    goToStep(idx + 1, 'stepping');
  }, [phase, scenario, goToStep, finishTour]);

  const goBack = useCallback(() => {
    if (phase === 'idle') return;
    const steps = scenario?.steps ?? [];
    if (phase === 'complete') {
      goToStep(Math.max(steps.length - 1, 0), 'stepping');
      return;
    }
    const idx = stepIndexRef.current;
    if (idx <= 0) return;
    goToStep(idx - 1, 'stepping');
  }, [phase, scenario, goToStep]);

  const canBack = phase === 'complete' || (playActive && stepIndex > 0);
  const canNext = phase !== 'complete';

  const startPlay = useCallback(() => {
    bumpPlayGen();
    clearPlayTimer();
    setStepIndex(0);
    setPhase('playing');
    setInspectOpen(false);
    setSelectedId(scenario?.steps[0]?.nodeId ?? null);
    if (mobile) setSheetExpanded(true);
  }, [scenario, mobile, bumpPlayGen, clearPlayTimer]);

  useEffect(() => {
    if (phase !== 'playing' || !currentStep) {
      clearPlayTimer();
      return;
    }
    const gen = playGenRef.current;
    timerRef.current = window.setTimeout(() => {
      if (gen !== playGenRef.current) return;
      advanceStep();
    }, currentStep.dwellMs);
    return clearPlayTimer;
  }, [phase, stepIndex, currentStep, advanceStep, clearPlayTimer]);

  useEffect(() => {
    if ((playInProgress || phase === 'complete') && currentStep && phase !== 'complete') {
      setSelectedId(currentStep.nodeId);
    }
  }, [stepIndex, playInProgress, phase, currentStep]);

  useEffect(() => {
    if (playInProgress && mobile) setSheetExpanded(true);
  }, [stepIndex, playInProgress, mobile]);

  useEffect(() => {
    if (playComplete && mobile) setSheetExpanded(false);
  }, [playComplete, mobile]);

  useEffect(() => () => clearPlayTimer(), [clearPlayTimer]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (inspectOpen) {
          setInspectOpen(false);
          return;
        }
        resetPlay();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [resetPlay, inspectOpen]);

  const onClearSelection = useCallback(() => {
    if (phase !== 'idle') return;
    setSelectedId(null);
    setInspectOpen(false);
  }, [phase]);

  const onNodeTap = useCallback(
    (id: string) => {
      if (phase === 'playing' || phase === 'stepping') return;
      if (phase === 'complete') {
        if (id !== scenario?.verdictNodeId) {
          setPhase('idle');
          setSelectedId(id);
          setInspectOpen(true);
          if (mobile) setSheetExpanded(true);
        } else {
          setInspectOpen(true);
        }
        return;
      }
      setSelectedId(id);
      setInspectOpen(true);
      if (mobile) setSheetExpanded(true);
    },
    [phase, scenario, mobile],
  );

  const playFocusActive = phase === 'playing' || phase === 'stepping' || phase === 'complete';

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
        phase={phase}
        canPlay={ready}
        onPlay={() => {
          if (phase === 'paused') setPhase('playing');
          else startPlay();
        }}
        onPause={() => {
          clearPlayTimer();
          setPhase('paused');
        }}
        onReset={resetPlay}
        onBack={goBack}
        onNext={goNext}
        stepIndex={displayStepIndex}
        stepCount={stepCount}
        canBack={canBack}
        canNext={canNext}
        mobile={mobile}
      />

      <main
        className={[
          'app-main',
          playActive && !mobile ? 'app-main--play' : '',
          !mobile && inspectOpen && inspectNode ? 'app-main--inspect' : '',
        ]
          .filter(Boolean)
          .join(' ')}
      >
        {playActive && !mobile && scenario ? (
          <PlayStepRail
            scenario={scenario}
            currentStep={displayStep}
            stepIndex={displayStepIndex}
            stepTotal={scenario.steps.length}
            nodeMeta={focusMeta}
          />
        ) : null}

        <div className="flow-wrap">
          <FunnelErrorBoundary onRetry={() => setResetToken((t) => t + 1)}>
            {ready ? (
              <FunnelDiagram
                baseNodes={baseNodes}
                baseEdges={baseEdges}
                focusId={focusId}
                hoverId={hoverId}
                selectedId={selectedId}
                phase={phase}
                stepPopupText={stepPopupText}
                playHighlight={playHighlight}
                hideLabels={hideLabels}
                mobile={mobile}
                playFocusActive={playFocusActive}
                selectionLocked={selectionLocked}
                resetToken={resetToken}
                ready={ready}
                onHover={setHoverId}
                onNodeTap={onNodeTap}
                onClearSelection={onClearSelection}
                onZoom={setZoom}
              />
            ) : (
              <div className="flow-loading">Laying out funnel…</div>
            )}
          </FunnelErrorBoundary>
        </div>

        {!mobile && inspectOpen && inspectNode ? (
          <InspectPanel node={inspectNode} open onClose={() => setInspectOpen(false)} />
        ) : null}

        {mobile ? (
          <MobilePlaySheet
            context={{
              mode: playActive ? 'play' : inspectOpen ? 'inspect' : 'idle',
              scenario: playActive ? scenario : undefined,
              currentStep: playActive ? displayStep : undefined,
              stepIndex: playActive ? displayStepIndex : undefined,
              stepTotal: playActive ? scenario?.steps.length : undefined,
            }}
            node={playActive ? focusMeta : inspectNode}
            playInProgress={playInProgress}
            playComplete={playComplete}
            showPlayContent={playActive}
            sheetExpanded={sheetExpanded}
            inspectOpen={inspectOpen}
            onToggleSheet={() => setSheetExpanded((v) => !v)}
            onCloseInspect={() => setInspectOpen(false)}
          />
        ) : null}
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

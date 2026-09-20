import type { PlayPhase } from '../playPhase';
import { isPlayInProgress } from '../playPhase';
import { PLAY_SCENARIOS } from '../scenarios';

type Props = {
  scenarioId: string;
  onScenarioChange: (id: string) => void;
  phase: PlayPhase;
  canPlay: boolean;
  onPlay: () => void;
  onPause: () => void;
  onReset: () => void;
  stepIndex: number;
  stepCount: number;
  mobile?: boolean;
};

export default function Toolbar({
  scenarioId,
  onScenarioChange,
  phase,
  canPlay,
  onPlay,
  onPause,
  onReset,
  stepIndex,
  stepCount,
  mobile = false,
}: Props) {
  const playing = phase === 'playing';
  const paused = phase === 'paused';

  return (
    <div className={`toolbar ${mobile ? 'toolbar--mobile' : ''}`}>
      <div className="toolbar-row toolbar-row--primary">
        <div className="toolbar-group toolbar-group--fixture">
          <label className="toolbar-label" htmlFor="fixture-select">
            Fixture
          </label>
          <select
            id="fixture-select"
            className="toolbar-select"
            value={scenarioId}
            onChange={(e) => onScenarioChange(e.target.value)}
            disabled={phase === 'playing'}
          >
            {PLAY_SCENARIOS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div className="toolbar-group toolbar-actions">
          {playing ? (
            <button type="button" className="toolbar-btn" onClick={onPause}>
              Pause
            </button>
          ) : (
            <button type="button" className="toolbar-btn toolbar-btn-primary" onClick={onPlay} disabled={!canPlay}>
              {paused ? 'Resume' : 'Play'}
            </button>
          )}
          <button type="button" className="toolbar-btn" onClick={onReset}>
            Reset
          </button>
        </div>
      </div>
      {isPlayInProgress(phase) && stepCount > 0 ? (
        <div className="toolbar-progress">
          Step {Math.min(stepIndex + 1, stepCount)} / {stepCount}
        </div>
      ) : null}
    </div>
  );
}

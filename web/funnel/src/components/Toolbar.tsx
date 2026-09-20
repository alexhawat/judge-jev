import { PLAY_SCENARIOS } from '../scenarios';

type Props = {
  scenarioId: string;
  onScenarioChange: (id: string) => void;
  playing: boolean;
  paused: boolean;
  canPlay: boolean;
  onPlay: () => void;
  onPause: () => void;
  onReset: () => void;
  stepIndex: number;
  stepCount: number;
};

export default function Toolbar({
  scenarioId,
  onScenarioChange,
  playing,
  paused,
  canPlay,
  onPlay,
  onPause,
  onReset,
  stepIndex,
  stepCount,
}: Props) {
  return (
    <div className="toolbar">
      <div className="toolbar-group">
        <label className="toolbar-label" htmlFor="fixture-select">
          Sample fixture
        </label>
        <select
          id="fixture-select"
          className="toolbar-select"
          value={scenarioId}
          onChange={(e) => onScenarioChange(e.target.value)}
          disabled={playing && !paused}
        >
          {PLAY_SCENARIOS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      <div className="toolbar-group toolbar-actions">
        {playing && !paused ? (
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
      {(playing || paused) && stepCount > 0 ? (
        <div className="toolbar-progress">
          Step {Math.min(stepIndex + 1, stepCount)} / {stepCount}
        </div>
      ) : null}
    </div>
  );
}

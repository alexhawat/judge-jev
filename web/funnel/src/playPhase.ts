export type PlayPhase = 'idle' | 'playing' | 'paused' | 'stepping' | 'complete';

export function isPlayActive(phase: PlayPhase): boolean {
  return phase !== 'idle';
}

/** Auto-play or manual step tour — shows rail, sheet, highlight, popups. */
export function isPlayInProgress(phase: PlayPhase): boolean {
  return phase === 'playing' || phase === 'paused' || phase === 'stepping';
}

export function isManualStepping(phase: PlayPhase): boolean {
  return phase === 'stepping';
}

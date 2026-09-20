export type PlayPhase = 'idle' | 'playing' | 'paused' | 'complete';

export function isPlayActive(phase: PlayPhase): boolean {
  return phase !== 'idle';
}

export function isPlayInProgress(phase: PlayPhase): boolean {
  return phase === 'playing' || phase === 'paused';
}

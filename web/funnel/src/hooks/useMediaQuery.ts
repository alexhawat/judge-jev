import { useEffect, useState } from 'react';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' ? window.matchMedia(query).matches : false,
  );

  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}

/** Narrow viewport or coarse pointer — treat as mobile/touch layout. */
export function useMobileLayout(): boolean {
  return useMediaQuery('(max-width: 900px), (pointer: coarse) and (max-width: 1200px)');
}

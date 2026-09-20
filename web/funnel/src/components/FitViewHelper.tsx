import { useEffect, useRef } from 'react';
import { useReactFlow } from '@xyflow/react';

/** Initial zoom-in after layout; full-funnel fitView when resetToken increments. */
export default function FitViewHelper({
  ready,
  resetToken,
}: {
  ready: boolean;
  resetToken: number;
}) {
  const { fitView, getViewport, setViewport } = useReactFlow();
  const didInitial = useRef(false);

  useEffect(() => {
    if (!ready) return;

    if (resetToken > 0) {
      void fitView({ padding: 0.1, duration: 380, minZoom: 0.55, maxZoom: 1.35 });
      return;
    }

    if (didInitial.current) return;
    didInitial.current = true;

    const id = window.requestAnimationFrame(() => {
      void fitView({ padding: 0.1, duration: 0, minZoom: 0.55, maxZoom: 1.35 });
      window.setTimeout(() => {
        const v = getViewport();
        const zoom = Math.min(Math.max(v.zoom * 1.18, 0.78), 1.28);
        setViewport({ x: v.x, y: v.y, zoom }, { duration: 180 });
      }, 50);
    });
    return () => window.cancelAnimationFrame(id);
  }, [ready, resetToken, fitView, getViewport, setViewport]);

  return null;
}

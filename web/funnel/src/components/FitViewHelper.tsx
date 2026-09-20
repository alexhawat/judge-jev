import { useEffect } from 'react';
import { useReactFlow } from '@xyflow/react';

/** Zoom in after initial layout so the vertical funnel fills the canvas. */
export default function FitViewHelper({ ready }: { ready: boolean }) {
  const { fitView, getViewport, setViewport } = useReactFlow();

  useEffect(() => {
    if (!ready) return;
    const id = window.requestAnimationFrame(() => {
      void fitView({ padding: 0.1, duration: 0, minZoom: 0.55, maxZoom: 1.35 });
      window.setTimeout(() => {
        const v = getViewport();
        const zoom = Math.min(Math.max(v.zoom * 1.18, 0.78), 1.28);
        setViewport({ x: v.x, y: v.y, zoom }, { duration: 180 });
      }, 50);
    });
    return () => window.cancelAnimationFrame(id);
  }, [ready, fitView, getViewport, setViewport]);

  return null;
}

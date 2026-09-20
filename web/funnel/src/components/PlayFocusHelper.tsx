import { useEffect } from 'react';
import { useReactFlow } from '@xyflow/react';

/** Pan/zoom to keep the active Play step node on screen (desktop + mobile). */
export default function PlayFocusHelper({
  nodeId,
  active,
}: {
  nodeId: string | null;
  active: boolean;
}) {
  const { getNode, fitView } = useReactFlow();

  useEffect(() => {
    if (!active || !nodeId) return;
    const node = getNode(nodeId);
    if (!node) return;

    const id = window.requestAnimationFrame(() => {
      void fitView({
        nodes: [{ id: nodeId }],
        padding: 0.42,
        duration: 360,
        minZoom: 0.6,
        maxZoom: 1.15,
      });
    });
    return () => window.cancelAnimationFrame(id);
  }, [nodeId, active, getNode, fitView]);

  return null;
}

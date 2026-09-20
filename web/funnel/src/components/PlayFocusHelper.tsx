import { useEffect } from 'react';
import { useReactFlow } from '@xyflow/react';

/** On mobile, pan/zoom to keep the active play step node on screen. */
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
        padding: 0.45,
        duration: 320,
        minZoom: 0.65,
        maxZoom: 1.15,
      });
    });
    return () => window.cancelAnimationFrame(id);
  }, [nodeId, active, getNode, fitView]);

  return null;
}

import { FUNNEL_EDGES } from './funnelData';

export type EdgeDef = { source: string; target: string; id?: string };

const INPUT_ID = 'input';

function adjacency(edges: EdgeDef[]) {
  const forward = new Map<string, string[]>();
  const backward = new Map<string, string[]>();
  for (const e of edges) {
    if (!forward.has(e.source)) forward.set(e.source, []);
    if (!backward.has(e.target)) backward.set(e.target, []);
    forward.get(e.source)!.push(e.target);
    backward.get(e.target)!.push(e.source);
  }
  return { forward, backward };
}

/** Shortest path from `from` to `to` (BFS). */
export function shortestPath(from: string, to: string, edges: EdgeDef[] = FUNNEL_EDGES): string[] {
  if (from === to) return [from];
  const { forward } = adjacency(edges);
  const queue: string[] = [from];
  const prev = new Map<string, string | null>([[from, null]]);

  while (queue.length) {
    const node = queue.shift()!;
    for (const next of forward.get(node) ?? []) {
      if (prev.has(next)) continue;
      prev.set(next, node);
      if (next === to) {
        const path: string[] = [to];
        let cur: string | null = to;
        while (cur && cur !== from) {
          cur = prev.get(cur) ?? null;
          if (cur) path.unshift(cur);
        }
        return path;
      }
      queue.push(next);
    }
  }
  return [from];
}

export function edgeKey(source: string, target: string) {
  return `${source}->${target}`;
}

/** Path edges along a node sequence. */
export function pathEdges(nodePath: string[], edges: EdgeDef[] = FUNNEL_EDGES): Set<string> {
  const ids = new Set<string>();
  for (let i = 0; i < nodePath.length - 1; i += 1) {
    const src = nodePath[i];
    const tgt = nodePath[i + 1];
    const match = edges.find((e) => e.source === src && e.target === tgt);
    if (match) ids.add(match.id ?? edgeKey(src, tgt));
    else ids.add(edgeKey(src, tgt));
  }
  return ids;
}

export function neighborsOf(nodeId: string, edges: EdgeDef[] = FUNNEL_EDGES): Set<string> {
  const out = new Set<string>();
  for (const e of edges) {
    if (e.source === nodeId) out.add(e.target);
    if (e.target === nodeId) out.add(e.source);
  }
  return out;
}

export interface HighlightResult {
  nodes: Set<string>;
  edgeIds: Set<string>;
  path: string[];
}

/**
 * Ancestor path from Input to focus, plus focus neighbors.
 * If focus is a verdict, extend to JudgmentResult when reachable.
 */
export function computeHighlight(
  focusId: string | null,
  edgeList: EdgeDef[] = FUNNEL_EDGES,
): HighlightResult {
  if (!focusId) {
    return { nodes: new Set(), edgeIds: new Set(), path: [] };
  }

  let path = shortestPath(INPUT_ID, focusId, edgeList);
  if (focusId.startsWith('verdict-') && !path.includes('result')) {
    const toResult = shortestPath(focusId, 'result', edgeList);
    if (toResult.length > 1) path = [...path, ...toResult.slice(1)];
  }

  const nodes = new Set(path);
  for (const n of neighborsOf(focusId, edgeList)) nodes.add(n);

  const ids = pathEdges(path, edgeList);
  for (const e of edgeList) {
    if (e.source === focusId || e.target === focusId) {
      ids.add(e.id ?? edgeKey(e.source, e.target));
    }
  }

  return { nodes, edgeIds: ids, path };
}

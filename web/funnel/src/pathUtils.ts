import { FUNNEL_EDGES } from './funnelData';

export type EdgeDef = { source: string; target: string; id?: string };

const INPUT_ID = 'input';

function adjacency(edges: EdgeDef[]) {
  const forward = new Map<string, string[]>();
  for (const e of edges) {
    if (!forward.has(e.source)) forward.set(e.source, []);
    forward.get(e.source)!.push(e.target);
  }
  return { forward };
}

/** Shortest forward path from `from` to `to` (BFS). */
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

export interface HighlightResult {
  nodes: Set<string>;
  edgeIds: Set<string>;
  path: string[];
}

const QUESTION_STAGES = new Set(['screen', 'profile', 'locate', 'score', 'route-q']);

/** Cumulative scenario deciding path for play mode — no shortest-path drift through unrelated branches. */
export function computePlayHighlight(
  decidingPath: string[],
  pathEndIndex: number,
  edgeList: EdgeDef[] = FUNNEL_EDGES,
): HighlightResult {
  if (!decidingPath.length || pathEndIndex < 0) {
    return { nodes: new Set(), edgeIds: new Set(), path: [] };
  }

  const path = decidingPath.slice(0, pathEndIndex + 1);
  const nodes = new Set<string>(path);
  const edgeIds = new Set<string>();

  for (let i = 0; i < path.length - 1; i += 1) {
    const a = path[i];
    const b = path[i + 1];
    const direct = edgeList.find((e) => e.source === a && e.target === b);
    if (direct) edgeIds.add(direct.id ?? edgeKey(a, b));
  }

  const hasAnswers = path.includes('answers');
  for (const stage of path) {
    if (!QUESTION_STAGES.has(stage)) continue;
    const fromJev = edgeList.find((e) => e.source === 'jev-call' && e.target === stage);
    if (fromJev) edgeIds.add(fromJev.id ?? edgeKey('jev-call', stage));
    if (hasAnswers) {
      const toAns = edgeList.find((e) => e.source === stage && e.target === 'answers');
      if (toAns) edgeIds.add(toAns.id ?? edgeKey(stage, 'answers'));
    }
  }

  return { nodes, edgeIds, path };
}

/** Hover/inspect: ancestor path from Input only — no fan-out into sibling verdicts/branches. */
export function computeHighlight(
  focusId: string | null,
  edgeList: EdgeDef[] = FUNNEL_EDGES,
): HighlightResult {
  if (!focusId) {
    return { nodes: new Set(), edgeIds: new Set(), path: [] };
  }

  let path = shortestPath(INPUT_ID, focusId, edgeList);
  if (focusId.startsWith('verdict-')) {
    const toResult = shortestPath(focusId, 'result', edgeList);
    if (toResult.length > 1 && !path.includes('result')) {
      path = [...path, ...toResult.slice(1)];
    }
  }

  return {
    nodes: new Set(path),
    edgeIds: pathEdges(path, edgeList),
    path,
  };
}

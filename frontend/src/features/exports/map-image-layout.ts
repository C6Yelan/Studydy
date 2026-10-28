type Edge = { source_concept_id: string; target_concept_id: string };

// 匯出保留先備層級，只在每層內找交叉較少的順序。
export function arrangeMapColumns(columns: string[][], edges: Edge[]) {
  let best = columns.map(ids => [...ids]);
  const positions = (layers: string[][]) => new Map(layers.flatMap((ids, x) => ids.map((id, y) =>
    [id, { x, y: y - (ids.length - 1) / 2 }] as const)));
  const crossings = (layers: string[][]) => {
    const points = positions(layers);
    const side = (a: { x: number; y: number }, b: { x: number; y: number }, c: { x: number; y: number }) =>
      (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    let count = 0;
    for (let i = 0; i < edges.length; i++) for (let j = i + 1; j < edges.length; j++) {
      const first = edges[i], second = edges[j];
      if (new Set([first.source_concept_id, first.target_concept_id, second.source_concept_id, second.target_concept_id]).size < 4) continue;
      const a = points.get(first.source_concept_id)!, b = points.get(first.target_concept_id)!;
      const c = points.get(second.source_concept_id)!, d = points.get(second.target_concept_id)!;
      if (side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0) count++;
    }
    return count;
  };
  let score = crossings(best);
  for (let sweep = 0; sweep < 6 && score > 0; sweep++) {
    const points = positions(best);
    const candidate = best.map(ids => [...ids]);
    for (const ids of candidate) {
      const center = (id: string) => {
        const neighbours = edges.flatMap(edge => edge.source_concept_id === id ? [edge.target_concept_id] : edge.target_concept_id === id ? [edge.source_concept_id] : [])
          .filter(other => points.get(other)!.x !== points.get(id)!.x);
        return neighbours.length ? neighbours.reduce((sum, other) => sum + points.get(other)!.y, 0) / neighbours.length : points.get(id)!.y;
      };
      ids.sort((a, b) => center(a) - center(b));
    }
    const candidateScore = crossings(candidate);
    if (candidateScore < score) { best = candidate; score = candidateScore; }
    for (const ids of best) for (let index = 0; index < ids.length - 1; index++) {
      [ids[index], ids[index + 1]] = [ids[index + 1], ids[index]];
      const changed = crossings(best);
      if (changed < score) score = changed;
      else [ids[index], ids[index + 1]] = [ids[index + 1], ids[index]];
    }
  }
  return best;
}

// 通道只在區間互不相交時共用；跨層與同層連線使用相同分配規則。
export function mapEdgeLanes(intervals: { id: string; start: number; end: number }[]) {
  const ends: number[] = [];
  const lanes = new Map<string, number>();
  for (const interval of [...intervals].sort((a, b) => a.start - b.start || a.end - b.end)) {
    let lane = ends.findIndex(end => end < interval.start);
    if (lane < 0) lane = ends.length;
    ends[lane] = interval.end;
    lanes.set(interval.id, lane);
  }
  return lanes;
}

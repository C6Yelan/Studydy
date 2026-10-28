export const MAP_NODE_WIDTH = 220;
const COLUMN_GAP = 90, ROW_GAP = 20, DUMMY_HEIGHT = 8, TARGET_ASPECT = 1.4;

type LayoutEdge = { id: string; source: string; target: string; prerequisite: boolean };
type Op = ["M", number, number] | ["L", number, number] | ["C", number, number, number, number, number, number];
export type MapLayout = {
  width: number; height: number;
  nodes: Map<string, { x: number; y: number; height: number }>;
  edges: Map<string, { ops: Op[]; arrow: { x: number; y: number; direction: 1 | -1 } }>;
  isolated: string[];
};
type Item = { key: string; id: string | null; height: number; rank: number; y: number };

// 先備關係決定層級；跨多層的連線在中間層放置透明節點，讓連線佔用自己的通道而不穿過概念。
// 層數過多時折成多列，使整張圖接近螢幕或 A4 的比例；孤立概念不參與排版，由呼叫端另列。
export function layoutMap(input: { nodes: { id: string; height: number; order: number }[]; edges: LayoutEdge[] }): MapLayout {
  const ids = new Set(input.nodes.map(n => n.id));
  const edges = input.edges.filter(e => e.source !== e.target && ids.has(e.source) && ids.has(e.target));
  const linked = new Set(edges.flatMap(e => [e.source, e.target]));
  const nodes = input.nodes.filter(n => linked.has(n.id)).sort((a, b) => a.order - b.order);
  const isolated = input.nodes.filter(n => !linked.has(n.id)).sort((a, b) => a.order - b.order).map(n => n.id);
  const result: MapLayout = { width: 0, height: 0, nodes: new Map(), edges: new Map(), isolated };
  if (!nodes.length) return result;

  const rank = new Map(nodes.map(n => [n.id, 0]));
  const pending = new Map(nodes.map(n => [n.id, edges.filter(e => e.prerequisite && e.target === n.id).length]));
  const queue = nodes.filter(n => pending.get(n.id) === 0).map(n => n.id);
  // 先備關係已由結構契約保證無環；其他關係只畫線，不改變學習層次。
  for (const id of queue) for (const e of edges.filter(e => e.prerequisite && e.source === id)) {
    rank.set(e.target, Math.max(rank.get(e.target)!, rank.get(id)! + 1));
    pending.set(e.target, pending.get(e.target)! - 1);
    if (pending.get(e.target) === 0) queue.push(e.target);
  }
  const rankCount = Math.max(...rank.values()) + 1;

  const items = new Map<string, Item>(nodes.map(n => [n.id, { key: n.id, id: n.id, height: n.height, rank: rank.get(n.id)!, y: 0 }]));
  const layers: Item[][] = Array.from({ length: rankCount }, () => []);
  nodes.forEach(n => layers[rank.get(n.id)!].push(items.get(n.id)!));
  const up = new Map<string, string[]>(), down = new Map<string, string[]>();
  const link = (a: string, b: string) => { (down.get(a) ?? down.set(a, []).get(a)!).push(b); (up.get(b) ?? up.set(b, []).get(b)!).push(a); };
  // 每條連線由低層到高層的項目串；高層指回低層的連線標為 reversed（箭頭畫在低層端）。
  const chains = new Map<string, { items: Item[]; reversed: boolean }>();
  const sameRank: LayoutEdge[] = [];
  for (const e of edges) {
    const rs = rank.get(e.source)!, rt = rank.get(e.target)!;
    if (rs === rt) { sameRank.push(e); continue; }
    const reversed = rs > rt;
    const [lo, hi] = reversed ? [e.target, e.source] : [e.source, e.target];
    const chain = [items.get(lo)!];
    for (let r = rank.get(lo)! + 1; r < rank.get(hi)!; r++) {
      const dummy: Item = { key: `${e.id}#${r}`, id: null, height: DUMMY_HEIGHT, rank: r, y: 0 };
      items.set(dummy.key, dummy); layers[r].push(dummy); chain.push(dummy);
    }
    chain.push(items.get(hi)!);
    chain.slice(1).forEach((item, i) => link(chain[i].key, item.key));
    chains.set(e.id, { items: chain, reversed });
  }

  const position = () => new Map(layers.flatMap(layer => layer.map((item, i) => [item.key, i] as const)));
  const crossings = () => {
    const pos = position();
    let count = 0;
    for (let r = 0; r < rankCount - 1; r++) {
      const segments = layers[r].flatMap(a => (down.get(a.key) ?? []).map(b => [pos.get(a.key)!, pos.get(b)!]));
      for (let i = 0; i < segments.length; i++) for (let j = i + 1; j < segments.length; j++)
        if ((segments[i][0] - segments[j][0]) * (segments[i][1] - segments[j][1]) < 0) count++;
    }
    return count;
  };
  const reorder = (r: number, neighbours: Map<string, string[]>) => {
    const pos = position();
    const score = (item: Item) => {
      const near = neighbours.get(item.key);
      return near?.length ? near.reduce((sum, key) => sum + pos.get(key)!, 0) / near.length : pos.get(item.key)!;
    };
    const keyed = layers[r].map(item => [score(item), item] as const);
    keyed.sort((a, b) => a[0] - b[0]);
    layers[r] = keyed.map(([, item]) => item);
  };
  let best = layers.map(layer => [...layer]), bestCrossings = crossings();
  for (let sweep = 0; sweep < 14 && bestCrossings > 0; sweep++) {
    for (let r = 1; r < rankCount; r++) reorder(r, up);
    for (let r = rankCount - 2; r >= 0; r--) reorder(r, down);
    const count = crossings();
    if (count < bestCrossings) { best = layers.map(layer => [...layer]); bestCrossings = count; }
  }
  best.forEach((layer, r) => { layers[r] = layer; });

  // 縱向座標：先堆疊，再讓每個項目靠近相鄰項目的中心，連線較直。
  const stack = (layer: Item[]) => {
    let top = 0;
    for (const item of layer) { item.y = top + item.height / 2; top += item.height + ROW_GAP; }
    const shift = (top - ROW_GAP) / 2;
    for (const item of layer) item.y -= shift;
  };
  layers.forEach(stack);
  const neighbourCentre = (item: Item) => {
    const near = [...(up.get(item.key) ?? []), ...(down.get(item.key) ?? [])];
    return near.length ? near.reduce((sum, key) => sum + items.get(key)!.y, 0) / near.length : item.y;
  };
  for (let pass = 0; pass < 10; pass++) {
    const order = pass % 2 ? [...layers.keys()].reverse() : [...layers.keys()];
    for (const r of order) {
      const wanted = layers[r].map(neighbourCentre);
      let top = -Infinity, error = 0;
      const placed = layers[r].map((item, i) => {
        const c = Math.max(wanted[i], top + item.height / 2);
        top = c + item.height / 2 + ROW_GAP; error += wanted[i] - c; return c;
      });
      const shift = error / placed.length;
      layers[r].forEach((item, i) => { item.y = placed[i] + shift; });
    }
  }

  // 同層連線以右側弧線表示；區間相疊的弧線使用不同通道。
  const pos = position();
  const arcLane = new Map<string, number>();
  const arcEnds = new Map<number, number[]>();
  for (const e of [...sameRank].sort((a, b) => Math.min(pos.get(a.source)!, pos.get(a.target)!) - Math.min(pos.get(b.source)!, pos.get(b.target)!))) {
    const start = Math.min(pos.get(e.source)!, pos.get(e.target)!), end = Math.max(pos.get(e.source)!, pos.get(e.target)!);
    const ends = arcEnds.get(rank.get(e.source)!) ?? arcEnds.set(rank.get(e.source)!, []).get(rank.get(e.source)!)!;
    let lane = ends.findIndex(last => last < start);
    if (lane < 0) lane = ends.length;
    ends[lane] = end; arcLane.set(e.id, lane);
  }
  const arcLanes = Math.max(0, ...[...arcLane.values()].map(l => l + 1));
  const gap = COLUMN_GAP + arcLanes * 10;

  // 依整體比例選擇列數；每列內仍由左至右。
  const boundaryCount = (perBand: number) => Array.from({ length: Math.ceil(rankCount / perBand) - 1 }, (_, b) => {
    const r = (b + 1) * perBand;
    return [...chains.values()].filter(c => c.items.some(item => item.rank === r) && c.items.some(item => item.rank === r - 1)).length;
  });
  const extents = (perBand: number) => Array.from({ length: Math.ceil(rankCount / perBand) }, (_, b) => {
    const members = layers.slice(b * perBand, (b + 1) * perBand).flat();
    return { top: Math.min(...members.map(i => i.y - i.height / 2)), bottom: Math.max(...members.map(i => i.y + i.height / 2)) };
  });
  const geometry = (perBand: number) => {
    const counts = boundaryCount(perBand), side = 40 + Math.max(0, ...counts) * 10 + (counts.length ? 24 : 0);
    const gutters = counts.map(n => 56 + n * 10), bands = extents(perBand);
    const width = side + perBand * MAP_NODE_WIDTH + (perBand - 1) * gap + side + 24 + arcLanes * 10;
    const height = bands.reduce((sum, b) => sum + b.bottom - b.top, 0) + gutters.reduce((a, b) => a + b, 0);
    return { counts, side, gutters, bands, width, height, perBand };
  };
  const options = Array.from({ length: Math.min(4, rankCount) }, (_, i) => geometry(Math.ceil(rankCount / (i + 1))))
    .filter((g, i, all) => all.findIndex(o => o.perBand === g.perBand) === i);
  const score = (g: { width: number; height: number }) => Math.abs(Math.log(g.width / (g.height + 220) / TARGET_ASPECT));
  const chosen = options.reduce((a, b) => score(b) < score(a) - 0.05 ? b : a);
  const { perBand, side, gutters, bands, counts } = chosen;
  result.width = chosen.width; result.height = chosen.height;
  const bandTop = bands.map((_, b) => bands.slice(0, b).reduce((sum, e, i) => sum + e.bottom - e.top + gutters[i], 0));
  const place = (item: Item) => {
    const band = Math.floor(item.rank / perBand);
    return { cx: side + (item.rank % perBand) * (MAP_NODE_WIDTH + gap) + MAP_NODE_WIDTH / 2, y: bandTop[band] + item.y - bands[band].top };
  };
  for (const item of items.values()) if (item.id) {
    const p = place(item);
    result.nodes.set(item.id, { x: p.cx - MAP_NODE_WIDTH / 2, y: p.y - item.height / 2, height: item.height });
  }

  // 同一組概念之間的多條連線，端點在概念邊上略微錯開。
  const pairs = new Map<string, string[]>();
  for (const e of edges) { const k = [e.source, e.target].sort().join("|"); (pairs.get(k) ?? pairs.set(k, []).get(k)!).push(e.id); }
  const portOffset = (e: LayoutEdge, item: Item) => {
    const group = pairs.get([e.source, e.target].sort().join("|"))!;
    return Math.max(-(item.height / 2 - 8), Math.min(item.height / 2 - 8, (group.indexOf(e.id) - (group.length - 1) / 2) * 10));
  };
  // 跨列段：由列尾右側繞到兩列間的通道，再由下一列左側進入；端點越高越外側，減少彼此交叉。
  type Crossing = { edge: string; from: Item; to: Item; offset: number; lane: number; entry: number };
  const crossingsByBoundary: Crossing[][] = counts.map(() => []);
  for (const [id, chain] of chains) chain.items.slice(1).forEach((to, i) => {
    const from = chain.items[i];
    if (to.rank % perBand === 0) crossingsByBoundary[Math.floor(to.rank / perBand) - 1].push({ edge: id, from, to, offset: 0, lane: 0, entry: 0 });
  });
  crossingsByBoundary.forEach((list, b) => {
    list.sort((x, y) => place(x.from).y - place(y.from).y);
    list.forEach((c, k) => { c.offset = 20 + (list.length - 1 - k) * 10; c.lane = (list.length - 1 - k) * 10; });
    [...list].sort((x, y) => place(x.to).y - place(y.to).y).forEach((c, k) => { c.entry = 20 + k * 10; });
    list.forEach(c => { c.lane += 20 + bandTop[b] + bands[b].bottom - bands[b].top; });
  });
  const rightEdge = side + perBand * MAP_NODE_WIDTH + (perBand - 1) * gap;
  for (const [id, chain] of chains) {
    const e = edges.find(edge => edge.id === id)!;
    const points = chain.items.map(item => ({ ...place(item), item }));
    const first = points[0], last = points.at(-1)!;
    const start = { x: first.cx + MAP_NODE_WIDTH / 2, y: first.y + portOffset(e, first.item) };
    const end = { x: last.cx - MAP_NODE_WIDTH / 2, y: last.y + portOffset(e, last.item) };
    const ops: Op[] = [["M", start.x, start.y]];
    let cursor = start;
    chain.items.slice(1).forEach((item, i) => {
      const final = i === chain.items.length - 2;
      const target = final ? end : { x: points[i + 1].cx - MAP_NODE_WIDTH / 2, y: points[i + 1].y };
      const crossing = item.rank % perBand === 0 ? crossingsByBoundary[Math.floor(item.rank / perBand) - 1].find(c => c.edge === id && c.to === item)! : null;
      if (crossing) {
        const outer = rightEdge + crossing.offset, inner = side - crossing.entry;
        ops.push(["L", outer, cursor.y], ["L", outer, crossing.lane], ["L", inner, crossing.lane], ["L", inner, target.y], ["L", target.x, target.y]);
      } else {
        const d = (target.x - cursor.x) * 0.5;
        ops.push(["C", cursor.x + d, cursor.y, target.x - d, target.y, target.x, target.y]);
      }
      // 透明節點佔滿整欄寬度，連線在該欄內水平直行，避免斜切過相鄰概念。
      if (!final) { cursor = { x: target.x + MAP_NODE_WIDTH, y: target.y }; ops.push(["L", cursor.x, cursor.y]); } else cursor = target;
    });
    result.edges.set(id, { ops, arrow: chain.reversed ? { ...start, direction: -1 } : { ...end, direction: 1 } });
  }
  for (const e of sameRank) {
    const s = items.get(e.source)!, t = items.get(e.target)!, a = place(s), b = place(t);
    const x = a.cx + MAP_NODE_WIDTH / 2, bulge = 26 + arcLane.get(e.id)! * 10;
    const ys = a.y + portOffset(e, s), yt = b.y + portOffset(e, t);
    result.edges.set(e.id, { ops: [["M", x, ys], ["C", x + bulge, ys, x + bulge, yt, x, yt]], arrow: { x, y: yt, direction: -1 } });
  }
  return result;
}

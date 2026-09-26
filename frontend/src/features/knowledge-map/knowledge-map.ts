import type { KnowledgeStructureView } from "../../api/contracts";

type MapNode = { id: string; x: number; y: number; width: number; depth: 0 | 1 | 2 };

// API 已驗證路徑完整包含教材概念；導覽只依正式順序排列。
export function learningNavigationItems(view: KnowledgeStructureView) {
  const byId = new Map(view.concepts.map(concept => [concept.concept_id, concept]));
  return [...view.initial_learning_path].sort((a, b) => a.position - b.position)
    .map(step => ({ concept: byId.get(step.concept_id)!, step }));
}

export function initialFocusConceptId(view: KnowledgeStructureView): string {
  const neighbours = new Map<string, Set<string>>();
  for (const relation of view.relations) {
    if (relation.source_concept_id === relation.target_concept_id) continue;
    for (const [id, other] of [[relation.source_concept_id, relation.target_concept_id], [relation.target_concept_id, relation.source_concept_id]]) {
      if (!neighbours.has(id)) neighbours.set(id, new Set());
      neighbours.get(id)!.add(other);
    }
  }
  // Prefer the earliest concept that connects several ideas, without changing the path.
  return view.initial_learning_path.find((step) => (neighbours.get(step.concept_id)?.size ?? 0) > 1)?.concept_id
    ?? view.initial_learning_path.find((step) => neighbours.has(step.concept_id))?.concept_id
    ?? view.initial_learning_path[0]?.concept_id ?? view.concepts[0]?.concept_id ?? "";
}

// 顯示預算只限制畫布，不改原始 KS；先保留近層，再加入第二層。
const MAX_MAP_NODES = 30;
const MAX_MAP_RELATIONS = 60;

export function focusGraph(view: KnowledgeStructureView, selectedId: string) {
  type Visit = { depth: 0 | 1 | 2; side: -1 | 0 | 1; parentRelation: string | null };
  const visits = new Map<string, Visit>();
  const adjacent = new Map<string, KnowledgeStructureView["relations"]>();
  for (const relation of view.relations) {
    for (const id of new Set([relation.source_concept_id, relation.target_concept_id])) {
      const list = adjacent.get(id) ?? [];
      list.push(relation);
      adjacent.set(id, list);
    }
  }
  if (view.concepts.length) visits.set(selectedId, { depth: 0, side: 0, parentRelation: null });
  // Map 依插入順序走訪，確保完整第一層優先於第二層；同一概念只加入一次。
  for (const [id, visit] of visits) {
    if (visit.depth === 2) continue;
    for (const relation of adjacent.get(id) ?? []) {
      const other = relation.source_concept_id === id ? relation.target_concept_id : relation.source_concept_id;
      if (visits.has(other)) continue;
      visits.set(other, { depth: visit.depth === 0 ? 1 : 2,
        side: visit.depth === 0 ? (relation.target_concept_id === id ? -1 : 1) : visit.side,
        parentRelation: relation.relation_id });
    }
  }
  const visible = new Map([...visits].slice(0, MAX_MAP_NODES));
  const eligibleRelations = view.relations.filter(relation => visits.has(relation.source_concept_id) && visits.has(relation.target_concept_id));
  // 先保留每個顯示節點通往中心的連線，避免連線上限製造假的孤立節點。
  const relationIds = new Set([...visible.values()].flatMap(visit => visit.parentRelation ? [visit.parentRelation] : []));
  const withinVisible = eligibleRelations.filter(relation => visible.has(relation.source_concept_id) && visible.has(relation.target_concept_id));
  for (const relation of [...withinVisible.filter(edge => edge.source_concept_id === selectedId || edge.target_concept_id === selectedId), ...withinVisible]) {
    if (relationIds.size >= MAX_MAP_RELATIONS) break;
    relationIds.add(relation.relation_id);
  }
  const relations = withinVisible.filter(relation => relationIds.has(relation.relation_id));
  const nodes: MapNode[] = visible.has(selectedId) ? [{ id: selectedId, x: 0, y: 0, width: 300, depth: 0 }] : [];
  for (const depth of [1, 2] as const) for (const side of [-1, 1] as const) {
    const group = [...visible].filter(([, visit]) => visit.depth === depth && visit.side === side);
    const firstColumns = Math.ceil([...visible.values()].filter(visit => visit.depth === 1 && visit.side === side).length / 8);
    const startX = depth === 1 ? 420 : 840 + Math.max(0, firstColumns - 1) * 320;
    group.forEach(([id], index) => {
      // 每欄最多八張卡，避免第二層變成過長直列，迫使整圖縮到無法閱讀。
      const column = Math.floor(index / 8);
      const rows = Math.min(8, group.length - column * 8);
      nodes.push({ id, depth, width: 260,
        x: side * (startX + column * 320),
        y: (index % 8 - (rows - 1) / 2) * 210 });
    });
  }
  return { nodes, relations, totalNodes: visits.size, totalRelations: eligibleRelations.length };
}

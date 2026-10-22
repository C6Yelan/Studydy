import type { KnowledgeStructureView, KnowledgeOutlineView, KnowledgeRegionsView } from "../../api/contracts";

type MapView = Pick<KnowledgeOutlineView, "relations" | "initial_learning_path"> & { concepts: Pick<KnowledgeStructureView["concepts"][number], "concept_id" | "label" | "aliases">[] };

type MapNode = { id: string; x: number; y: number; width: number; depth: 0 | 1 | 2 };

// 章節數超過此值且有兩個以上來源檔時，地圖改為兩層：來源檔＝島群、章節＝島（千頁教材約 460 章）。
// 一般教材章節不多，維持一層，避免每個島群只有一兩章。
export const TWO_LEVEL_MIN_REGIONS = 24;

export type MapRegions = {
  basis: KnowledgeRegionsView["basis"];
  partial: boolean;
  regions: { id: string; label: string; index: number; conceptIds: string[]; group: number | null }[];
  // 空陣列表示一層呈現；兩層時區域已依島群排列，同群相鄰。
  groups: { id: string; label: string; index: number; regionIndexes: number[]; conceptCount: number }[];
  byConcept: Map<string, number>;
};

// 區域必須剛好涵蓋這份地圖的全部概念；版本或內容不一致時不分區，維持原本呈現。
export function mapRegions(view: Pick<MapView, "concepts">, value: KnowledgeRegionsView | null): MapRegions | null {
  if (!value) return null;
  const sourceGroups = value.groups ?? [];
  const twoLevel = sourceGroups.length >= 2 && value.regions.length > TWO_LEVEL_MIN_REGIONS;
  const groupIndex = new Map(sourceGroups.map((group, index) => [group.group_id, index]));
  const ordered = twoLevel
    ? [...value.regions].sort((a, b) => groupIndex.get(a.group_id!)! - groupIndex.get(b.group_id!)!)
    : value.regions;
  const byConcept = new Map<string, number>();
  ordered.forEach((region, index) => region.concept_ids.forEach((id) => byConcept.set(id, index)));
  if (byConcept.size !== view.concepts.length || !view.concepts.every((concept) => byConcept.has(concept.concept_id)))
    return null;
  const regions = ordered.map((region, index) => ({
    id: region.region_id, label: region.label, index, conceptIds: region.concept_ids,
    group: twoLevel ? groupIndex.get(region.group_id!)! : null,
  }));
  const groups = twoLevel
    ? sourceGroups
      .map((group, index) => {
        const members = regions.filter((region) => region.group === index);
        return {
          id: group.group_id, label: group.label, index, regionIndexes: members.map((region) => region.index),
          conceptCount: members.reduce((sum, region) => sum + region.conceptIds.length, 0),
        };
      })
      .filter((group) => group.regionIndexes.length > 0)
    : [];
  return { basis: value.basis, partial: value.partial ?? false, regions, groups, byConcept };
}

/** 島嶼配色：兩層時同一島群同色，一層時依區域。 */
export function regionColorIndex(regions: MapRegions, regionIndex: number) {
  return regions.regions[regionIndex]?.group ?? regionIndex;
}

/** 分區依據說明；章節為模型整理並經程式驗證，部分退回時明講。 */
export function regionBasisText(regions: MapRegions) {
  if (regions.basis === "chapters")
    return (regions.groups.length ? "依內容整理的章節，來源檔為島群" : "依教材內容整理的章節")
      + (regions.partial ? "；部分依來源檔分區" : "");
  return regions.basis === "source" ? "依各概念的主要來源檔分區" : "依學習順序分段，非教材章節";
}

// API 已驗證路徑完整包含教材概念；導覽只依正式順序排列。
export function learningNavigationItems(view: MapView) {
  const byId = new Map(view.concepts.map((concept) => [concept.concept_id, concept]));
  return [...view.initial_learning_path]
    .sort((a, b) => a.position - b.position)
    .map((step) => ({ concept: byId.get(step.concept_id)!, step }));
}

export function initialFocusConceptId(view: MapView): string {
  const neighbours = new Map<string, Set<string>>();
  for (const relation of view.relations) {
    if (relation.source_concept_id === relation.target_concept_id) continue;
    for (const [id, other] of [
      [relation.source_concept_id, relation.target_concept_id],
      [relation.target_concept_id, relation.source_concept_id],
    ]) {
      if (!neighbours.has(id)) neighbours.set(id, new Set());
      neighbours.get(id)!.add(other);
    }
  }
  // Prefer the earliest concept that connects several ideas, without changing the path.
  return (
    view.initial_learning_path.find((step) => (neighbours.get(step.concept_id)?.size ?? 0) > 1)
      ?.concept_id ??
    view.initial_learning_path.find((step) => neighbours.has(step.concept_id))?.concept_id ??
    view.initial_learning_path[0]?.concept_id ??
    view.concepts[0]?.concept_id ??
    ""
  );
}

// 顯示預算只限制畫布，不改原始 KS；先保留近層，再加入第二層。
const MAX_MAP_NODES = 30;
const MAX_MAP_RELATIONS = 60;

export function focusGraph(view: MapView, selectedId: string) {
  type Visit = { depth: 0 | 1 | 2; side: -1 | 0 | 1; parentRelation: string | null };
  const visits = new Map<string, Visit>();
  const adjacent = new Map<string, KnowledgeOutlineView["relations"]>();
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
      const other =
        relation.source_concept_id === id ? relation.target_concept_id : relation.source_concept_id;
      if (visits.has(other)) continue;
      visits.set(other, {
        depth: visit.depth === 0 ? 1 : 2,
        side: visit.depth === 0 ? (relation.target_concept_id === id ? -1 : 1) : visit.side,
        parentRelation: relation.relation_id,
      });
    }
  }
  const visible = new Map([...visits].slice(0, MAX_MAP_NODES));
  const eligibleRelations = view.relations.filter(
    (relation) => visits.has(relation.source_concept_id) && visits.has(relation.target_concept_id),
  );
  // 先保留每個顯示節點通往中心的連線，避免連線上限製造假的孤立節點。
  const relationIds = new Set(
    [...visible.values()].flatMap((visit) => (visit.parentRelation ? [visit.parentRelation] : [])),
  );
  const withinVisible = eligibleRelations.filter(
    (relation) =>
      visible.has(relation.source_concept_id) && visible.has(relation.target_concept_id),
  );
  for (const relation of [
    ...withinVisible.filter(
      (edge) => edge.source_concept_id === selectedId || edge.target_concept_id === selectedId,
    ),
    ...withinVisible,
  ]) {
    if (relationIds.size >= MAX_MAP_RELATIONS) break;
    relationIds.add(relation.relation_id);
  }
  const relations = withinVisible.filter((relation) => relationIds.has(relation.relation_id));
  const nodes: MapNode[] = visible.has(selectedId)
    ? [{ id: selectedId, x: 0, y: 0, width: 300, depth: 0 }]
    : [];
  for (const depth of [1, 2] as const)
    for (const side of [-1, 1] as const) {
      const group = [...visible].filter(
        ([, visit]) => visit.depth === depth && visit.side === side,
      );
      const firstColumns = Math.ceil(
        [...visible.values()].filter((visit) => visit.depth === 1 && visit.side === side).length /
          8,
      );
      const startX = depth === 1 ? 420 : 840 + Math.max(0, firstColumns - 1) * 320;
      group.forEach(([id], index) => {
        // 每欄最多八張卡，避免第二層變成過長直列，迫使整圖縮到無法閱讀。
        const column = Math.floor(index / 8);
        const rows = Math.min(8, group.length - column * 8);
        nodes.push({
          id,
          depth,
          width: 260,
          x: side * (startX + column * 320),
          y: ((index % 8) - (rows - 1) / 2) * 210,
        });
      });
    }
  return { nodes, relations, totalNodes: visits.size, totalRelations: eligibleRelations.length };
}

// 學習導覽一次最多畫出的概念列數；超過兩倍才分段，一般教材維持完整清單。
export const NAVIGATOR_WINDOW = 120;
export type NavigatorRange = { start: number; end: number };

/** 目前範圍仍包含選取概念時沿用（含使用者展開過的範圍）；否則以選取概念為中心重新取一段。 */
export function navigatorRange(current: NavigatorRange | null, selectedIndex: number, total: number): NavigatorRange {
  if (total <= NAVIGATOR_WINDOW * 2) return current && current.start === 0 && current.end === total ? current : { start: 0, end: total };
  if (current && selectedIndex >= current.start && selectedIndex < current.end && current.end <= total) return current;
  const start = Math.max(0, Math.min(selectedIndex - NAVIGATOR_WINDOW / 2, total - NAVIGATOR_WINDOW));
  return { start, end: Math.min(total, start + NAVIGATOR_WINDOW) };
}

/** 依顯示範圍切出各區域的可見項目；完全不在範圍內的區域不畫。 */
export function groupSlices<T extends { items: unknown[] }>(groups: T[], range: NavigatorRange) {
  const slices: { group: T; slice: T["items"] }[] = [];
  let offset = 0;
  for (const group of groups) {
    const from = Math.max(range.start, offset), to = Math.min(range.end, offset + group.items.length);
    if (from < to) slices.push({ group, slice: group.items.slice(from - offset, to - offset) });
    offset += group.items.length;
  }
  return slices;
}

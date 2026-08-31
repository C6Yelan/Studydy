import type { KnowledgeMapView } from "../../api/contracts";

export type RelationType = KnowledgeMapView["relations"][number]["type"];

export type RelationPresentation = {
  className: string;
  directional: boolean;
  label: string;
  explanation: string;
};

export type MapNode = {
  conceptId: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

const mapNodeSize = { width: 190, height: 88 };
const nodeGap = 20;
const groupGap = 70;

export function relationPresentation(type: RelationType): RelationPresentation {
  if (type === "prerequisite") return {
    className: "is-prerequisite",
    directional: true,
    label: "先備關係",
    explanation: "來源概念需要先學，再進入目標概念。",
  };
  if (type === "contains") return {
    className: "is-contains",
    directional: true,
    label: "組成關係",
    explanation: "來源概念包含目標概念作為內容的一部分。",
  };
  return {
    className: "is-related",
    directional: false,
    label: "互相關聯",
    explanation: "兩個概念在教材中互有關聯，沒有單向學習箭頭。",
  };
}

export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

export function hierarchyLayout(view: KnowledgeMapView): MapNode[] {
  const groupOrder = new Map(view.topology.flat_groups.map((group, index) => [
    group.flat_group_id, index,
  ]));
  const orderedNodes = [...view.topology.nodes].sort((left, right) => {
    const groupDifference = Number(groupOrder.get(left.flat_group_id))
      - Number(groupOrder.get(right.flat_group_id));
    if (groupDifference !== 0) return groupDifference;
    if (left.depth !== right.depth) return left.depth - right.depth;
    if (left.flat_group_anchor.page_number !== right.flat_group_anchor.page_number) {
      return left.flat_group_anchor.page_number - right.flat_group_anchor.page_number;
    }
    if (left.flat_group_anchor.reading_order !== right.flat_group_anchor.reading_order) {
      return left.flat_group_anchor.reading_order - right.flat_group_anchor.reading_order;
    }
    if (left.flat_group_anchor.evidence_id !== right.flat_group_anchor.evidence_id) {
      return left.flat_group_anchor.evidence_id.localeCompare(
        right.flat_group_anchor.evidence_id,
      );
    }
    return left.formal_concept_id.localeCompare(right.formal_concept_id);
  });
  const groupOffsets = new Map<string, number>();
  let nextGroupX = 0;
  for (const group of view.topology.flat_groups) {
    groupOffsets.set(group.flat_group_id, nextGroupX);
    const levelCounts = new Map<number, number>();
    for (const node of orderedNodes) {
      if (node.flat_group_id !== group.flat_group_id) continue;
      levelCounts.set(node.depth, (levelCounts.get(node.depth) ?? 0) + 1);
    }
    const widestLevel = Math.max(1, ...levelCounts.values());
    const occupiedWidth = widestLevel * mapNodeSize.width
      + (widestLevel - 1) * nodeGap;
    nextGroupX += occupiedWidth + groupGap;
  }
  const levelCounts = new Map<string, number>();
  return orderedNodes.map((node) => {
    const levelKey = `${node.flat_group_id}:${node.depth}`;
    const index = levelCounts.get(levelKey) ?? 0;
    levelCounts.set(levelKey, index + 1);
    return {
      conceptId: node.formal_concept_id,
      x: Number(groupOffsets.get(node.flat_group_id))
        + index * (mapNodeSize.width + nodeGap),
      y: node.depth * 150,
      ...mapNodeSize,
    };
  });
}

export function isPrimaryHierarchyRelation(
  view: KnowledgeMapView,
  relation: KnowledgeMapView["relations"][number],
): boolean {
  if (relation.type !== "contains") return false;
  return view.topology.nodes.some((node) =>
    node.formal_concept_id === relation.target_formal_concept_id
    && node.primary_parent_formal_concept_id === relation.source_formal_concept_id);
}

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
  const groupIndex = new Map(view.topology.flat_groups.map((group, index) => [
    group.flat_group_id, index,
  ]));
  const levelCounts = new Map<string, number>();
  return view.topology.nodes.map((node) => {
    const levelKey = `${node.flat_group_id}:${node.depth}`;
    const index = levelCounts.get(levelKey) ?? 0;
    levelCounts.set(levelKey, index + 1);
    return {
      conceptId: node.formal_concept_id,
      x: Number(groupIndex.get(node.flat_group_id)) * 260 + index * 210,
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

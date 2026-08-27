import type { KnowledgeMapView } from "../../api/contracts";

export type RelationType = KnowledgeMapView["relations"][number]["type"];

export type RelationPresentation = {
  className: string;
  directional: boolean;
  label: string;
  explanation: string;
};

export type FocusNode = {
  conceptId: string;
  x: number;
  y: number;
};

export type FocusNeighborhood = {
  nodes: FocusNode[];
  relations: KnowledgeMapView["relations"];
};

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

export function focusNeighborhood(view: KnowledgeMapView, selectedConceptId: string): FocusNeighborhood {
  const relations = view.relations.filter((relation) =>
    relation.source_formal_concept_id === selectedConceptId
    || relation.target_formal_concept_id === selectedConceptId);
  const neighborIds = [...new Set(relations.map((relation) =>
    relation.source_formal_concept_id === selectedConceptId
      ? relation.target_formal_concept_id
      : relation.source_formal_concept_id))];
  const nodes = [
    { conceptId: selectedConceptId, x: 50, y: 50 },
    ...neighborIds.map((conceptId, index) => {
      const angle = (Math.PI * 2 * index / neighborIds.length) - Math.PI / 2;
      return {
        conceptId,
        x: 50 + Math.cos(angle) * 38,
        y: 50 + Math.sin(angle) * 36,
      };
    }),
  ];
  return { nodes, relations };
}

export function learningPathReason(view: KnowledgeMapView, conceptId: string): string {
  const concept = view.concepts.find((item) => item.formal_concept_id === conceptId);
  const prerequisite = view.relations.find((relation) =>
    relation.type === "prerequisite"
    && !relation.is_in_prerequisite_cycle
    && relation.target_formal_concept_id === conceptId);
  if (prerequisite) {
    const source = view.concepts.find((item) =>
      item.formal_concept_id === prerequisite.source_formal_concept_id);
    return `先理解「${source?.label ?? "前一步概念"}」，再進入這個概念。`;
  }
  return `目前沒有可用的非循環先備關係；依教材首次出現的第 ${concept?.source_page_numbers[0] ?? "?"} 頁安排。`;
}

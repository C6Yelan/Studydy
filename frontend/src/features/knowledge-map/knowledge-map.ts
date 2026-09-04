import dagre from "@dagrejs/dagre";

import type { KnowledgeStructureView, RelationType } from "../../api/contracts";

export type MapNode = {
  id: string;
  kind: "material" | "section" | "concept";
  x: number;
  y: number;
  width: number;
  height: number;
};

export type MapConnector = {
  id: string;
  source: string;
  target: string;
  type: "tree" | RelationType;
  reason?: string;
};

const sizes = {
  material: { width: 220, height: 72 },
  section: { width: 210, height: 76 },
  concept: { width: 190, height: 88 },
};

export function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

export function documentTreeConnectors(view: KnowledgeStructureView): MapConnector[] {
  const rootId = view.document_tree.material_id;
  return [
    ...view.document_tree.sections.map((section) => ({
      id: `root:${section.section_id}`,
      source: rootId,
      target: section.section_id,
      type: "tree" as const,
    })),
    ...view.document_tree.sections.flatMap((section) =>
      section.concept_ids.map((conceptId) => ({
        id: `section:${section.section_id}:${conceptId}`,
        source: section.section_id,
        target: conceptId,
        type: "tree" as const,
      }))),
  ];
}

export function relationConnectors(view: KnowledgeStructureView): MapConnector[] {
  return view.relations.map((relation) => ({
    id: relation.relation_id,
    source: relation.source_concept_id,
    target: relation.target_concept_id,
    type: relation.type,
    reason: relation.learner_reason,
  }));
}

export function hierarchyLayout(view: KnowledgeStructureView): MapNode[] {
  const rootId = view.document_tree.material_id;
  const nodes: Array<Pick<MapNode, "id" | "kind">> = [
    { id: rootId, kind: "material" },
    ...view.document_tree.sections.map((section) => ({
      id: section.section_id,
      kind: "section" as const,
    })),
    ...view.concepts.map((concept) => ({
      id: concept.concept_id,
      kind: "concept" as const,
    })),
  ];
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "TB", nodesep: 28, ranksep: 64, marginx: 24, marginy: 24 });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) graph.setNode(node.id, { ...sizes[node.kind] });
  for (const connector of documentTreeConnectors(view)) {
    graph.setEdge(connector.source, connector.target);
  }
  dagre.layout(graph);
  return nodes.map((node) => {
    const position = graph.node(node.id) as { x: number; y: number };
    const size = sizes[node.kind];
    return {
      ...node,
      x: position.x - size.width / 2,
      y: position.y - size.height / 2,
      ...size,
    };
  });
}

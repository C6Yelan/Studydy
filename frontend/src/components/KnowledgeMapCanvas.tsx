import {
  Background,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import type { KnowledgeMapResponse } from "../api/types";
import { ConceptNode, type ConceptNodeData } from "./ConceptNode";
import { RelationEdge, type RelationEdgeData } from "./RelationEdge";

interface KnowledgeMapCanvasProps {
  map: KnowledgeMapResponse;
}

const nodeTypes = {
  concept: ConceptNode,
};

const edgeTypes = {
  concept_relation: RelationEdge,
};

export function KnowledgeMapCanvas({ map }: KnowledgeMapCanvasProps) {
  const nodes = useMemo<Node<ConceptNodeData>[]>(
    () =>
      map.nodes.map((node) => ({
        id: node.id,
        type: "concept",
        position: node.position,
        data: node.data,
        draggable: false,
      })),
    [map.nodes],
  );

  const edges = useMemo<Edge<RelationEdgeData>[]>(
    () =>
      map.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: "concept_relation",
        label: edge.label,
        data: edge.data,
        markerEnd: {
          type: MarkerType.ArrowClosed,
        },
      })),
    [map.edges],
  );

  return (
    <div className="map-canvas" aria-label="Knowledge map canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesReconnectable={false}
        fitView
        fitViewOptions={{ padding: 0.2 }}
      >
        <Background />
      </ReactFlow>
    </div>
  );
}

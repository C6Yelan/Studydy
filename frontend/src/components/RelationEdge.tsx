import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";

import type { KnowledgeMapEdge } from "../api/types";

export type RelationEdgeData = KnowledgeMapEdge["data"] & Record<string, unknown>;
export type RelationEdgeType = Edge<RelationEdgeData, "concept_relation">;

export function RelationEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  markerEnd,
  data,
}: EdgeProps<RelationEdgeType>) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        className={data?.needs_review ? "relation-edge relation-edge-review" : "relation-edge"}
      />
      <EdgeLabelRenderer>
        <div
          className={data?.needs_review ? "relation-label relation-label-review" : "relation-label"}
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
        >
          <span>{data?.relation_type ?? "relation"}</span>
          {data?.needs_review ? <strong>Needs review</strong> : null}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

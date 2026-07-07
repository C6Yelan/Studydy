import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import type { KnowledgeMapNode } from "../api/types";

export type ConceptNodeData = KnowledgeMapNode["data"] & Record<string, unknown>;
type ConceptNodeType = Node<ConceptNodeData, "concept">;

export function ConceptNode({ data, selected }: NodeProps<ConceptNodeType>) {
  return (
    <article
      className={[
        "concept-node",
        data.needs_review ? "concept-node-review" : "",
        selected ? "concept-node-selected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="concept-node-header">
        <h3>{data.label}</h3>
        {data.needs_review ? <span className="review-marker">Needs review</span> : null}
      </div>
      {data.summary ? <p>{data.summary}</p> : null}
      <dl className="concept-node-meta">
        <div>
          <dt>Difficulty</dt>
          <dd>{data.difficulty_level ?? "Pending"}</dd>
        </div>
        <div>
          <dt>Score</dt>
          <dd>{data.score_value ?? "Pending"}</dd>
        </div>
      </dl>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </article>
  );
}

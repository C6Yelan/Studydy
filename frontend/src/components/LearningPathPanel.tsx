import type { LearningPathResponse } from "../api/types";

interface LearningPathPanelProps {
  path: LearningPathResponse | null;
  error: string | null;
  isLoading: boolean;
}

export function LearningPathPanel({ path, error, isLoading }: LearningPathPanelProps) {
  return (
    <aside className="learning-path-panel" aria-live="polite">
      <div className="learning-path-header">
        <div>
          <p className="summary-label">Learning Path</p>
          <h2>Recommended order</h2>
        </div>
        {path ? <span className="path-status">Learning path ready</span> : null}
      </div>

      {isLoading ? <p className="state-text">Loading learning path...</p> : null}

      {error ? (
        <div className="state-block state-block-error">
          <h3>Unable to load learning path</h3>
          <p>Learning path is unavailable right now.</p>
        </div>
      ) : null}

      {!isLoading && !error && path && path.needs_review ? (
        <div className="path-review-block">
          <span className="review-marker">Needs review</span>
          {path.review_reason ? <p>{path.review_reason}</p> : null}
        </div>
      ) : null}

      {!isLoading && !error && (!path || path.nodes.length === 0) ? (
        <div className="state-block">
          <h3>No learning path yet</h3>
          <p>No recommended path is available for this material yet.</p>
        </div>
      ) : null}

      {!isLoading && !error && path && path.nodes.length > 0 ? (
        <ol className="learning-path-list">
          {path.nodes.map((node) => (
            <li key={`${node.order_index}-${node.concept_id}`}>
              <span className="path-order">{node.order_index}</span>
              <div className="path-item-body">
                <div className="path-item-header">
                  <h3>{node.concept_name}</h3>
                  <span className="path-requirement">
                    {node.is_required ? "Required" : "Optional"}
                  </span>
                </div>
                <p>{node.reason ?? "Reason pending"}</p>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </aside>
  );
}

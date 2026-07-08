import type { LearningPathResponse } from "../api/types";

interface LearningPathPanelProps {
  path: LearningPathResponse | null;
  error: string | null;
  isLoading: boolean;
  selectedConceptId: number | null;
  onConceptSelect: (conceptId: number) => void;
}

export function LearningPathPanel({
  path,
  error,
  isLoading,
  selectedConceptId,
  onConceptSelect,
}: LearningPathPanelProps) {
  const selectedConceptIsInPath =
    selectedConceptId !== null &&
    path !== null &&
    path.nodes.some((node) => node.concept_id === selectedConceptId);

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
        <>
          {selectedConceptId !== null && !selectedConceptIsInPath ? (
            <p className="path-selection-note">Not in current Learning Path</p>
          ) : null}
          <ol className="learning-path-list">
            {path.nodes.map((node) => {
              const isSelected = node.concept_id === selectedConceptId;

              return (
                <li key={`${node.order_index}-${node.concept_id}`}>
                  <button
                    className={`path-item${isSelected ? " path-item-selected" : ""}`}
                    type="button"
                    aria-current={isSelected ? "step" : undefined}
                    onClick={() => onConceptSelect(node.concept_id)}
                  >
                    <span className="path-order">{node.order_index}</span>
                    <span className="path-item-body">
                      <span className="path-item-header">
                        <span className="path-item-title">{node.concept_name}</span>
                        <span className="path-requirement">
                          {node.is_required ? "Required" : "Optional"}
                        </span>
                      </span>
                      <span className="path-item-reason">
                        {node.reason ?? "Reason pending"}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </>
      ) : null}
    </aside>
  );
}

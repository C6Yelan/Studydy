import type { ConceptDetailResponse, RelationSummary } from "../api/types";

interface ConceptDetailPanelProps {
  detail: ConceptDetailResponse | null;
  error: string | null;
  isLoading: boolean;
  selectedConceptId: number | null;
  conceptLabels: Record<number, string>;
}

function relationLabel(
  relation: RelationSummary,
  direction: "incoming" | "outgoing",
  conceptLabels: Record<number, string>,
) {
  const conceptId =
    direction === "incoming" ? relation.source_concept_id : relation.target_concept_id;
  const conceptLabel = conceptLabels[conceptId] ?? `Concept #${conceptId}`;

  return `${relation.relation_type} ${conceptLabel}`;
}

function learningPathPositionLabel(position: number | null) {
  return position === null ? "Not in current Learning Path" : `#${position} in Learning Path`;
}

export function ConceptDetailPanel({
  detail,
  error,
  isLoading,
  selectedConceptId,
  conceptLabels,
}: ConceptDetailPanelProps) {
  if (selectedConceptId === null) {
    return (
      <aside className="detail-panel">
        <p className="summary-label">Concept Detail</p>
        <div className="state-block">
          <h3>Select a concept</h3>
          <p>Click a map node to load concept detail.</p>
        </div>
      </aside>
    );
  }

  if (isLoading) {
    return (
      <aside className="detail-panel" aria-live="polite">
        <p className="summary-label">Concept Detail</p>
        <p className="state-text">Loading concept #{selectedConceptId}...</p>
      </aside>
    );
  }

  if (error) {
    return (
      <aside className="detail-panel" aria-live="polite">
        <p className="summary-label">Concept Detail</p>
        <div className="state-block state-block-error">
          <h3>Unable to load concept</h3>
          <p>{error}</p>
        </div>
      </aside>
    );
  }

  if (!detail) {
    return null;
  }

  const { concept } = detail;

  return (
    <aside className="detail-panel">
      <div className="detail-header">
        <p className="summary-label">Concept Detail</p>
        {concept.needs_review ? <span className="review-marker">Needs review</span> : null}
      </div>
      <h2>{concept.name}</h2>
      {concept.summary ? <p className="detail-summary">{concept.summary}</p> : null}

      <dl className="detail-grid">
        <div>
          <dt>Difficulty</dt>
          <dd>{concept.difficulty_level ?? "Pending"}</dd>
        </div>
        <div>
          <dt>Importance</dt>
          <dd>{concept.importance_level ?? "Pending"}</dd>
        </div>
        <div>
          <dt>Mastery</dt>
          <dd>{detail.mastery_status}</dd>
        </div>
        <div>
          <dt>Path Position</dt>
          <dd>{learningPathPositionLabel(detail.learning_path_position)}</dd>
        </div>
      </dl>

      <section className="detail-section">
        <h3>Keywords</h3>
        {concept.keywords.length > 0 ? (
          <ul className="tag-list">
            {concept.keywords.map((keyword) => (
              <li key={keyword}>{keyword}</li>
            ))}
          </ul>
        ) : (
          <p>No keywords yet.</p>
        )}
      </section>

      <section className="detail-section">
        <h3>Evidence</h3>
        {detail.evidence_list.length > 0 ? (
          <ul className="detail-list">
            {detail.evidence_list.map((evidence) => (
              <li key={evidence.id}>
                <strong>{evidence.evidence_type}</strong>
                <span>{evidence.quote_text ?? "No quote text"}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No evidence yet.</p>
        )}
      </section>

      <section className="detail-section">
        <h3>Related Concepts</h3>
        <div className="relation-groups">
          <div>
            <h4>Incoming</h4>
            {detail.incoming_relations.length > 0 ? (
              <ul className="detail-list compact-list">
                {detail.incoming_relations.map((relation) => (
                  <li key={relation.id}>
                    {relationLabel(relation, "incoming", conceptLabels)}
                    {relation.reason ? (
                      <span className="relation-reason">{relation.reason}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p>None.</p>
            )}
          </div>
          <div>
            <h4>Outgoing</h4>
            {detail.outgoing_relations.length > 0 ? (
              <ul className="detail-list compact-list">
                {detail.outgoing_relations.map((relation) => (
                  <li key={relation.id}>
                    {relationLabel(relation, "outgoing", conceptLabels)}
                    {relation.reason ? (
                      <span className="relation-reason">{relation.reason}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p>None.</p>
            )}
          </div>
        </div>
      </section>

      <section className="detail-section">
        <h3>Resources</h3>
        <p>{detail.resource_list.length} linked resources.</p>
      </section>

      {detail.warnings.length > 0 ? (
        <section className="detail-section">
          <h3>Warnings</h3>
          <ul className="detail-list compact-list">
            {detail.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </aside>
  );
}

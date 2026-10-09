import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { VisualConceptCard } from "./VisualConceptCard";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { cardRelations, hasCardEvidence } from "./card-relation";

export function Flashcard({ card, structure, materialName, flipped, onFlip, apiClient, sourceResolver }: {
  card: ConceptCard;
  structure: KnowledgeStructureView;
  materialName: string;
  flipped: boolean;
  onFlip: () => void;
  apiClient: StudydyApiClient;
  sourceResolver: string;
}) {
  const relations = cardRelations(card, structure);
  const unresolved = structure.relations.filter(r => (r.source_concept_id === card.concept_id || r.target_concept_id === card.concept_id)
    && !relations.some(item => item.relation.relation_id === r.relation_id));
  const availableEvidence = structure.concepts.flatMap(c => c.claims.flatMap(claim => claim.evidence)).filter(hasCardEvidence);
  const review = structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain";
  if (!flipped) return <VisualConceptCard card={card} structure={structure} materialName={materialName} onDetails={onFlip} />;
  return (
    <div className="flashcard is-back">
        <article className="flashcard-back" aria-label={`${card.label}的重點`}>
          <span className="flashcard-kicker">概念重點</span>
          {review && <span className="flashcard-review">教材版本待複核</span>}
          <h2>{card.label}</h2>
          <div className="flashcard-content" tabIndex={0} aria-label="卡片重點內容">
            {card.claims.map((claim, index) => (
              <section className="flashcard-point" key={claim.claim_id}>
                <span className="flashcard-point-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                <p
                  className={claim.evidence.some((item) => item.kind === "code") ? "is-code" : undefined}
                  tabIndex={claim.evidence.some((item) => item.kind === "code") ? 0 : undefined}
                >{claimText(claim)}</p>
              </section>
            ))}
            {relations.map(relation => <section key={relation.relation.relation_id} className="flashcard-relation" aria-label="圖卡關係與來源">
              <h3>教材關係</h3>
              <p>{relation.source.label} ／ {relation.target.label}</p>
              <p>{relation.relation.learner_reason}</p>
              <div className="flashcard-source-links">{sourceLinks(relation.evidence).map(evidence => <SourceButton key={evidence.evidence_id}
                apiClient={apiClient} resolver={sourceResolver} evidence={evidence} label={`關係來源 · 第 ${evidence.normalized_page ?? evidence.page} 頁`} />)}</div>
            </section>)}
            {unresolved.map(relation => <section key={relation.relation_id} className="flashcard-relation" aria-label="待複核的教材關係">
              <h3>教材關係 · 來源不完整，待複核</h3>
              <p>{relation.learner_reason || "此關係尚無說明。"}</p>
              <p>未用於圖卡構圖；以下僅列可回查的來源。</p>
              <div className="flashcard-source-links">{sourceLinks(availableEvidence.filter(e => relation.evidence_refs.includes(e.evidence_id))).map(evidence =>
                <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence} />)}</div>
            </section>)}
            <section className="flashcard-sources" aria-label="概念重點來源">
              <h3>來源</h3>
              <div className="flashcard-source-links">
                {sourceLinks(card.claims.flatMap(claim => claim.evidence)).map(evidence => (
                  <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence}
                    label={evidence.source_name ? `${evidence.source_name} · 第 ${evidence.normalized_page ?? evidence.page} 頁` : undefined} />
                ))}
              </div>
            </section>
          </div>
        </article>
    </div>
  );
}

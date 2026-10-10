import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { VisualConceptCard } from "./VisualConceptCard";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { cardContent } from "./card-content";
import { cardRelations, hasCardEvidence } from "./card-relation";

export function Flashcard({ card, structure, materialName, apiClient, sourceResolver, pageIndex }: {
  card: ConceptCard;
  structure: KnowledgeStructureView;
  materialName: string;
  apiClient: StudydyApiClient;
  sourceResolver: string;
  pageIndex?: number;
}) {
  const relations = cardRelations(card, structure);
  const content = cardContent(card, structure);
  const shownEvidence = [...card.claims.flatMap(claim => claim.evidence), ...(content?.kind === "group" ? content.items.flatMap(item => item.evidence) : [])];
  const unresolved = structure.relations.filter(r => (r.source_concept_id === card.concept_id || r.target_concept_id === card.concept_id)
    && !relations.some(item => item.relation.relation_id === r.relation_id));
  const availableEvidence = structure.concepts.flatMap(c => c.claims.flatMap(claim => claim.evidence)).filter(hasCardEvidence);
  const review = structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain";
  return (
    <div className="concept-card">
      <VisualConceptCard card={card} structure={structure} materialName={materialName} pageIndex={pageIndex} />
      <details className="concept-card-details">
        <summary>完整重點與來源</summary>
        <div className="flashcard">
          <article className="flashcard-details" aria-label={`${card.label}的重點`}>
            <span className="flashcard-kicker">概念重點</span>
            {review && <span className="flashcard-review">教材版本待複核</span>}
            <h2>{card.label}</h2>
            {card.claims.some(claim => !claim.evidence.length || claim.evidence.some(e => !hasCardEvidence(e))) && <p className="flashcard-review">部分重點缺少可回查來源。</p>}
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
                <h3>教材關係 · 待複核</h3>
                <p>{relation.learner_reason || "此關係尚無說明。"}</p>
                <p>此關係的方向或來源需要確認；以下列出可回查的來源。</p>
                <div className="flashcard-source-links">{sourceLinks(availableEvidence.filter(e => relation.evidence_refs.includes(e.evidence_id))).map(evidence =>
                  <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence} />)}</div>
              </section>)}
              <section className="flashcard-sources" aria-label="概念重點來源">
                <h3>來源</h3>
                <div className="flashcard-source-links">
                  {sourceLinks(shownEvidence).map(evidence => (
                    <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence}
                      label={evidence.source_name ? `${evidence.source_name} · 第 ${evidence.normalized_page ?? evidence.page} 頁` : undefined} />
                  ))}
                </div>
              </section>
            </div>
          </article>
        </div>
      </details>
    </div>
  );
}

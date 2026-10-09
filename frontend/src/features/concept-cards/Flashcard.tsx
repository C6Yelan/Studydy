import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { VisualConceptCard } from "./VisualConceptCard";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { cardRelation } from "./card-relation";

export function Flashcard({ card, structure, materialName, flipped, onFlip, apiClient, sourceResolver }: {
  card: ConceptCard;
  structure: KnowledgeStructureView;
  materialName: string;
  flipped: boolean;
  onFlip: () => void;
  apiClient: StudydyApiClient;
  sourceResolver: string;
}) {
  const relation = cardRelation(card, structure);
  const review = structure.status.quality === "needs_review" || structure.status.processing !== "succeeded" || structure.status.decision !== "retain";
  if (!flipped) return <VisualConceptCard card={card} structure={structure} materialName={materialName} onDetails={onFlip} />;
  return (
    <div className="flashcard is-back">
        <article className="flashcard-back" aria-label={`${card.label}的重點`}>
          <span className="flashcard-kicker">概念重點</span>
          {review && <span className="flashcard-review">待確認 · 原教材尚未通過檢查</span>}
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
            {relation && <section className="flashcard-relation" aria-label="圖卡關係與來源">
              <h3>教材關係</h3>
              <p>{relation.source.label} ／ {relation.target.label}</p>
              <p>{relation.relation.learner_reason}</p>
              <div className="flashcard-source-links">{sourceLinks(relation.evidence).map(evidence => <SourceButton key={evidence.evidence_id}
                apiClient={apiClient} resolver={sourceResolver} evidence={evidence} label={`關係來源 · 第 ${evidence.normalized_page ?? evidence.page} 頁`} />)}</div>
            </section>}
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

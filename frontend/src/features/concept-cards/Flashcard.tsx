import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { Icon } from "../../ui/Icon";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";
import { cardRelation, ConceptVisual } from "./ConceptVisual";

export function Flashcard({ card, structure, flipped, onFlip, apiClient, sourceResolver }: {
  card: ConceptCard;
  structure: KnowledgeStructureView;
  flipped: boolean;
  onFlip: () => void;
  apiClient: StudydyApiClient;
  sourceResolver: string;
}) {
  const relation = cardRelation(card, structure);
  const textPoint = card.claims.find(claim => !claim.evidence.some(e => e.kind === "code" || e.kind === "formula"));
  const summary = textPoint ? claimText(textPoint) : "";
  const excerpt = Array.from(summary).slice(0, 100).join("");
  const review = structure.status.quality === "needs_review";
  return (
    <div className={`flashcard ${flipped ? "is-back" : "is-front"}`}>
      {!flipped ? (
        <button className="flashcard-front" type="button" onClick={onFlip} aria-label={`翻卡查看「${card.label}」的重點`}>
          <span className="flashcard-kicker"><Icon name="learning" size={18} /> 概念圖卡</span>
          {review && <span className="flashcard-review">待確認 · 原教材尚未通過檢查</span>}
          <span className="flashcard-title">{card.label}</span>
          <ConceptVisual card={card} structure={structure} />
          {summary && <span className="flashcard-summary">{excerpt}{excerpt !== summary && "…（節錄）"}</span>}
          <span className="flashcard-prompt"><Icon name="refresh" size={16} /> 翻面查看完整重點與來源</span>
        </button>
      ) : (
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
      )}
    </div>
  );
}

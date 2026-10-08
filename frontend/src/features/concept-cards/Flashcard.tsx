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
  const excerpt = (text: string, limit: number) => {
    const first = text.split(/(?<=[。！？])\s*/u)[0] ?? text;
    const characters = Array.from(first);
    return characters.length > limit ? characters.slice(0, limit).join('') + '…（節錄）' : first;
  };
  const definition = excerpt(summary, 140);
  const points = card.claims.slice(0, 4);
  const review = structure.status.quality === "needs_review";
  return (
    <div className={`flashcard ${flipped ? "is-back" : "is-front"}`}>
      {!flipped ? (
        <article className="flashcard-front" aria-label={`${card.label}概念卡`}>
          <span className="flashcard-kicker"><Icon name="learning" size={18} /> 概念卡</span>
          {review && <span className="flashcard-review">待確認 · 原教材尚未通過檢查</span>}
          <h2 className="flashcard-title">{card.label}</h2>
          {definition && <p className="flashcard-definition">{definition}</p>}
          <div className="flashcard-overview">
            <ul className="flashcard-bullets">{points.map(claim => <li key={claim.claim_id}>{excerpt(claimText(claim), 90)}</li>)}</ul>
            <ConceptVisual card={card} structure={structure} />
          </div>
          <footer className="flashcard-footer">
            <div className="flashcard-source-links">{sourceLinks(points.flatMap(claim=>claim.evidence)).slice(0,2).map(evidence=><SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence} />)}</div>
            <button className="text-button" type="button" onClick={onFlip} aria-label={`查看「${card.label}」的完整重點與來源`}>完整重點與來源</button>
          </footer>
        </article>
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

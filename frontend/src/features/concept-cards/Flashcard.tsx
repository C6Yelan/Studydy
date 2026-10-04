import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { Icon } from "../../ui/Icon";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";

export function Flashcard({ card, flipped, onFlip, apiClient, sourceResolver }: {
  card: ConceptCard;
  flipped: boolean;
  onFlip: () => void;
  apiClient: StudydyApiClient;
  sourceResolver: string;
}) {
  return (
    <div className={`flashcard ${flipped ? "is-back" : "is-front"}`}>
      {!flipped ? (
        <button className="flashcard-front" type="button" onClick={onFlip} aria-label={`翻卡查看「${card.label}」的重點`}>
          <span className="flashcard-kicker"><Icon name="learning" size={18} /> 概念</span>
          <span className="flashcard-title">{card.label}</span>
          <span className="flashcard-prompt"><Icon name="refresh" size={16} /> 點擊卡片，查看重點</span>
        </button>
      ) : (
        <article className="flashcard-back" aria-label={`${card.label}的重點`}>
          <span className="flashcard-kicker">概念重點</span>
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

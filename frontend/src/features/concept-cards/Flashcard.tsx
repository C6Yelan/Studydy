import type { StudydyApiClient } from "../../api/client";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
import { claimText } from "../../ui/claim-text";
import { Icon } from "../../ui/Icon";
import { SourceButton, sourceLinks } from "../../ui/SourceButton";

export function Flashcard({ card, flipped, onFlip, apiClient, sourceResolver, sourceQuality }: {
  card: ConceptCard;
  flipped: boolean;
  onFlip: () => void;
  apiClient: StudydyApiClient;
  sourceResolver: string;
  sourceQuality?: Pick<KnowledgeStructureView, "status" | "excluded_pages">;
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
          <header className="flashcard-back-header">
            <span className="flashcard-kicker">概念重點</span>
            <button className="text-button" type="button" onClick={onFlip}>查看正面</button>
          </header>
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
            <details className="flashcard-sources">
              <summary>查看來源</summary>
              {sourceQuality && (sourceQuality.status.quality === "needs_review" || sourceQuality.excluded_pages.length > 0) && (
                <p className="cards-source-note">來源狀態：{sourceQuality.status.quality === "needs_review" ? "部分內容待確認" : "已完成檢核"}。{sourceQuality.excluded_pages.length > 0 && `未納入頁碼：${sourceQuality.excluded_pages.map((page) => page.page).join("、")}。`}</p>
              )}
              {card.claims.map((claim, index) => (
                <div key={claim.claim_id}>
                  <strong>重點 {index + 1}</strong>
                  {sourceLinks(claim.evidence).map((evidence) => (
                    <SourceButton key={evidence.evidence_id} apiClient={apiClient} resolver={sourceResolver} evidence={evidence} />
                  ))}
                </div>
              ))}
            </details>
          </div>
        </article>
      )}
    </div>
  );
}

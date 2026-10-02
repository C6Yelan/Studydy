import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { StudydyApiClient } from "../../api/client";
import type { ConceptCardsView, EvidenceView } from "../../api/contracts";
import { SourceButton } from "../../ui/SourceButton";

type Card = ConceptCardsView["cards"][number];

function LiteralText({ text, evidence }: { text: string; evidence: EvidenceView[] }) {
  return evidence.some(item => item.kind === "code")
    ? <pre className="card-literal" tabIndex={0}>{text}</pre>
    : <p className="card-text">{text}</p>;
}

function Sources({ card, view, apiClient, close }: { card: Card; view: ConceptCardsView; apiClient: StudydyApiClient; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close(); }, []);
  const dismiss = () => { ref.current?.close(); close(); };
  return <dialog ref={ref} className="card-source-dialog" aria-label="圖卡教材來源" onCancel={e => { e.preventDefault(); dismiss(); }}>
    <header><h2>{card.label} · 教材來源</h2><button className="secondary-button" onClick={dismiss}>返回圖卡</button></header>
    {card.claims.map((claim, index) => <section className="card-evidence" key={claim.claim_id}>
      <h3>重點 {index + 1} 的來源</h3>
      {!claim.evidence.length && <p role="status">此重點缺少可回查證據，請確認原教材。</p>}
      {claim.evidence.map(evidence => <div key={evidence.evidence_id}>
        <SourceButton apiClient={apiClient} resolver={view.source_resolver} evidence={evidence} />
        <details><summary>檢視證據節錄</summary><LiteralText text={evidence.quote} evidence={[evidence]} />
          <p>教材第 {evidence.page} 頁 · 區塊順序 {evidence.block_order}</p>
          <details><summary>來源識別與位置</summary><p className="card-text">{evidence.evidence_id}</p>
            <p className="card-text">{evidence.source_locator.block_id}</p><p>{evidence.source_locator.region.join(", ")}</p></details>
        </details>
      </div>)}
    </section>)}
  </dialog>;
}

export function Flashcard({ card, view, apiClient }: { card: Card; view: ConceptCardsView; apiClient: StudydyApiClient }) {
  const [flipped, setFlipped] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const sourceButton = useRef<HTMLButtonElement>(null);
  const frontButton = useRef<HTMLButtonElement>(null);
  const backTitle = useRef<HTMLHeadingElement>(null);
  const interacted = useRef(false);
  const review = view.status.quality === "needs_review" || view.status.processing === "partial" || view.excluded_pages.length > 0;
  useEffect(() => { if (interacted.current) (flipped ? backTitle.current : frontButton.current)?.focus(); }, [flipped]);
  return <div className="flashcard-stack">
    {!flipped ? <button ref={frontButton} className="flashcard-face flashcard-front" aria-label={`翻卡：${card.label}，查看重點`}
      onClick={() => { interacted.current = true; setFlipped(true); }}>
      <span className="flashcard-side">概念</span><span className="flashcard-concept">{card.label}</span>
      <span className="flashcard-hint">點擊或按 Enter 查看重點 ↻</span>
      {review && <span className="flashcard-review-badge">教材有內容待確認</span>}
    </button> : <article className="flashcard-face flashcard-back" aria-label={`${card.label} 的核心重點`}>
      <div className="flashcard-answer">
        <p className="flashcard-side">核心重點</p><h2 ref={backTitle} tabIndex={-1}>{card.label}</h2>
        {review && <aside className="card-quality" aria-label="教材品質提示"><p role="status">此教材有內容待確認，複習時建議一併查看來源。</p>
          <details><summary>檢視品質詳情</summary><p>{view.status.quality} · {view.status.processing} · {view.status.decision}</p>
            <p>{view.status.reason_codes.join("、")}</p>
            {view.excluded_pages.map(p => <p key={p.page_ref}>第 {p.page} 頁未納入：{p.reason_code}</p>)}
          </details></aside>}
        {!card.claims.length && <p>此概念尚無已發布重點。</p>}
        <ul className="flashcard-claims">{card.claims.map(claim => <li key={claim.claim_id}><LiteralText text={claim.text} evidence={claim.evidence} /></li>)}</ul>
      </div>
      <footer><button className="secondary-button" onClick={() => setFlipped(false)}>翻回正面 ↻</button>
        <button ref={sourceButton} className="text-button" onClick={() => setSourceOpen(true)}>查看教材來源</button></footer>
    </article>}
    {sourceOpen && createPortal(<Sources card={card} view={view} apiClient={apiClient} close={() => { setSourceOpen(false); sourceButton.current?.focus(); }} />, document.body)}
  </div>;
}

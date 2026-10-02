import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { ConceptCardsView, EvidenceView, KnowledgeStructureView, RelationType } from "../../api/contracts";
import { SourceButton } from "../../ui/SourceButton";
import "./styles.css";

const relationLabels: Record<RelationType, string> = {
  prerequisite: "先備", part_of: "組成", application: "應用", example: "例子", contrast: "對照",
};

function LiteralText({ text, evidence }: { text: string; evidence: EvidenceView[] }) {
  // 不解析 Markdown、不改寫空白或技術字面值；code 只在自己的區域捲動。
  return evidence.some((item) => item.kind === "code")
    ? <pre className="card-literal" tabIndex={0}>{text}</pre>
    : <p className="card-text">{text}</p>;
}

function Evidence({ items, resolver, apiClient }: {
  items: EvidenceView[]; resolver: string; apiClient: StudydyApiClient;
}) {
  return items.length ? <details className="card-evidence">
    <summary>展開證據與來源（{items.length}）</summary>
    {items.map((evidence) => <section key={evidence.evidence_id}>
      <p>教材第 {evidence.page} 頁 · 區塊順序 {evidence.block_order}</p>
      <LiteralText text={evidence.quote} evidence={[evidence]} />
      <details><summary>來源識別與位置</summary>
        <p className="card-text">{evidence.evidence_id}</p>
        <p className="card-text">{evidence.source_locator.block_id}</p>
        <p>位置：{evidence.source_locator.region.join(", ")}</p>
      </details>
      <SourceButton apiClient={apiClient} resolver={resolver} evidence={evidence} />
    </section>)}
  </details> : <p role="status">證據不足：此項目沒有可回查的來源。</p>;
}

export function ConceptCardContent({ view, apiClient }: {
  view: ConceptCardsView; apiClient: StudydyApiClient;
}) {
  const selected = new Set(view.selection.concept_ids);
  const betweenSelected = view.relations.some((r) => selected.has(r.source_concept_id) && selected.has(r.target_concept_id));
  return <>
    <p>手動選取 {view.cards.length} 個概念，依教材發布順序排列。</p>
    <div className="card-quality" role="status">
      <strong>{view.status.quality === "needs_review" ? "教材內容待確認（needs_review）" : "教材品質：已接受"}</strong>
      <span>處理：{view.status.processing} · 決策：{view.status.decision}</span>
      {view.status.reason_codes.length > 0 && <p>{view.status.reason_codes.join("、")}</p>}
      {view.excluded_pages.map((page) => <p key={page.page_ref}>
        第 {page.page} 頁未納入：{page.reason_code}。請回查原教材。
      </p>)}
    </div>
    {view.cards.length > 1 && !betweenSelected &&
      <p className="card-text">所選概念之間沒有已發布關係；以下並列已有重點，不補比較內容。</p>}
    <div className="concept-card-grid">
      {view.cards.map((card) => <article className="concept-card surface" key={card.concept_id} aria-label={`${card.label} 觀念圖卡`}>
        <h3>{card.label}</h3>
        {card.claims.length === 0 && <p>尚無已發布重點，證據不足。</p>}
        {card.claims.map((claim, index) => <details className="card-claim" key={claim.claim_id} open={index === 0}>
          <summary>教材重點 {index + 1}（共 {card.claims.length} 項）</summary>
          <LiteralText text={claim.text} evidence={claim.evidence} />
          <Evidence items={claim.evidence} resolver={view.source_resolver} apiClient={apiClient} />
        </details>)}
      </article>)}
    </div>
    <section aria-label="已發布關係" className="card-relations">
      <h3>已發布關係</h3>
      {!view.relations.length && <p>這些概念沒有已發布關係；不推論先備或比較。</p>}
      {view.relations.map((relation) => <article className="concept-card surface" key={relation.relation_id}>
        <h4>{relationLabels[relation.type]}（{relation.type}）</h4>
        <p className="card-text">來源概念：{relation.source_label} → 目標概念：{relation.target_label}</p>
        {(!selected.has(relation.source_concept_id) || !selected.has(relation.target_concept_id)) &&
          <p>此關係包含未選取的概念。</p>}
        {relation.learner_reason
          ? <p className="card-text">{relation.learner_reason}</p>
          : <p>教材未提供關係理由。</p>}
        <Evidence items={relation.evidence} resolver={view.source_resolver} apiClient={apiClient} />
      </article>)}
    </section>
  </>;
}

function CardsDialog({ apiClient, materialId, map, close }: {
  apiClient: StudydyApiClient; materialId: string; map: KnowledgeStructureView; close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [cards, setCards] = useState<ConceptCardsView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const contentTitle = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => { requestVersion.current++; element.close(); };
  }, []);
  useEffect(() => { if (cards) contentTitle.current?.focus(); }, [cards]);
  const dismiss = () => {
    requestVersion.current++;
    // 先解除 modal 的 inert 邊界，原地圖按鈕才可接回鍵盤焦點。
    dialog.current?.close();
    close();
  };
  const open = async () => {
    const version = ++requestVersion.current;
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.getConceptCards({ materialId, structureRevision: map.knowledge_structure_revision }, selected);
      if (version !== requestVersion.current) return;
      if (result.selection.content_material_id !== map.material_id) throw new Error("圖卡的教材內容身分不一致。");
      setCards(result);
    } catch (error) {
      if (version === requestVersion.current) setError(errorMessage(error));
    } finally {
      if (version === requestVersion.current) setBusy(false);
    }
  };
  return <dialog ref={dialog} className="concept-cards-dialog" aria-label="觀念圖卡"
    onCancel={(event) => { event.preventDefault(); dismiss(); }}>
    <header className="card-dialog-header">
      <h2 ref={contentTitle} tabIndex={-1}>觀念圖卡</h2>
      <button className="secondary-button" type="button" onClick={dismiss}>返回知識地圖</button>
    </header>
    {cards ? <>
      <button className="secondary-button" type="button" onClick={() => { setCards(null); setError(null); }}>調整選取</button>
      <ConceptCardContent view={cards} apiClient={apiClient} />
    </> : <>
      <p>選取一個或多個概念，查看教材原有重點與來源。</p>
      <fieldset className="card-selection" disabled={busy}>
        <legend>選取概念</legend>
        {map.concepts.map((concept) => <label key={concept.concept_id}>
          <input type="checkbox" checked={selected.includes(concept.concept_id)}
            onChange={(event) => setSelected((ids) => event.target.checked
              ? [...ids, concept.concept_id] : ids.filter((id) => id !== concept.concept_id))} />
          <span>{concept.label}</span>
          {selected.includes(concept.concept_id) && <strong>已選取</strong>}
        </label>)}
      </fieldset>
      <p role="status">{busy ? "正在讀取固定版本圖卡…" : `已選取 ${selected.length} 個概念`}</p>
      {error && <div role="alert" className="form-error">
        <p>無法讀取這個版本的圖卡。{error}</p>
        <p>若版本已不可用，請返回教材庫重新開啟地圖並選取概念。</p>
      </div>}
      <button className="primary-button" type="button" disabled={busy || !selected.length} onClick={() => void open()}>
        {error ? "重試開啟圖卡" : "開啟圖卡"}
      </button>
    </>}
  </dialog>;
}

export function ConceptCards({ apiClient, materialId, map }: {
  apiClient: StudydyApiClient; materialId: string; map: KnowledgeStructureView;
}) {
  const [open, setOpen] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  return <>
    <button ref={opener} className="secondary-button" type="button" aria-haspopup="dialog"
      onClick={() => setOpen(true)}>選取觀念圖卡</button>
    {open && createPortal(<CardsDialog apiClient={apiClient} materialId={materialId} map={map}
      close={() => { setOpen(false); opener.current?.focus(); }} />, document.body)}
  </>;
}

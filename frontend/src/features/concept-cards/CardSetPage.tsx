import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardSetView, KnowledgeStructureView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { ProcessingTimeline } from "../material-flow/ProcessingTimeline";
import { Flashcard } from "./Flashcard";
import { useConceptArtwork } from "./VisualConceptCard";

export function CardSetPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId: string; materialId?: string }) {
  const [view, setView] = useState<CardSetView | null>(null);
  const [structure, setStructure] = useState<KnowledgeStructureView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setError(null);
    setView(null);
    setStructure(null);
    void (async () => {
      try {
        let result = await apiClient.getCardSet(cardSetId);
        if (cancelled) return;
        if (!materialId) { writeRoute({ name: "card-set", cardSetId, materialId: result.material_id }, true); return; }
        if (result.material_id !== materialId) throw new Error("這個卡組不屬於此教材。");
        setView(result);
        while (result.generation.status !== "ready") {
          if (result.generation.status === "failed") return;
          await new Promise<void>(resolve => { timer = setTimeout(resolve, 2000); });
          if (cancelled) return;
          const generation = await apiClient.getCardGeneration(cardSetId);
          if (cancelled) return;
          result = { ...result, generation };
          setView(result);
        }
        result = await apiClient.getCardSet(cardSetId);
        if (cancelled) return;
        // 圖像只讀卡組保存的版本，不追隨教材目前的 head。
        const retained = await apiClient.getKnowledgeStructure({ materialId, structureRevision: result.knowledge_structure_revision });
        if (cancelled) return;
        setView(result);
        setStructure(retained);
      } catch (failure) { if (!cancelled) setError(errorMessage(failure)); }
    })();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, cardSetId, materialId, reload]);
  const back = () => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" });
  return <section className="cards-page cards-study-page">
    <button className="cards-back text-button" type="button" onClick={back}><Icon name="arrow-left" size={17} /> 返回卡組</button>
    {view && view.generation.status !== "ready" && !error ? <CardGenerationProgress view={view} onRetry={() => {
      void apiClient.retryCardGeneration(cardSetId).then(() => setReload(n => n + 1)).catch(failure => setError(errorMessage(failure)));
    }} /> : error || !view || !structure ? <StateView title={error ? "無法開啟卡組" : "正在讀取概念卡"} description={error ?? "正在載入已保存的重點。"} tone={error ? "failure" : "loading"} live={!error} action={error ? <button className="primary-button" type="button" onClick={() => setReload((n) => n + 1)}>重新讀取</button> : undefined} />
      : <CardStudy key={`${cardSetId}:${view.version}`} structure={structure} view={view} apiClient={apiClient} materialId={materialId} />}
  </section>;
}

function CardGenerationProgress({ view, onRetry }: { view: CardSetView; onRetry: () => void }) {
  const generation = view.generation;
  const paused = generation.status === "failed";
  const stages = [
    { label: "準備來源", icon: "book" as const },
    { label: "AI 精簡", icon: "cards" as const },
    { label: "核對來源", icon: "check" as const },
    { label: "完成卡組", icon: "check" as const },
  ];
  const stage = ["preparing", "compacting", "checking", "complete"].indexOf(generation.stage);
  const activity = paused ? "已中斷" : generation.status === "pending" ? "排隊中" : "處理中";
  // 各觀念會重複精簡與核對；百分比只計已完成觀念，不猜測模型呼叫的內部進度。
  const percent = Math.floor(generation.completed / generation.total * 100);
  return <section className="is-processing" aria-label="概念卡生成狀態">
    <header className="cards-study-heading"><h1>{view.name}</h1></header>
    <div className="processing-grid">
      <section className="surface processing-card">
        <div className="processing-status" aria-live="polite">
          <div className="progress-heading overall-heading"><h2>{paused ? "概念卡生成暫停" : "正在生成概念卡"}</h2><strong>{percent}%</strong></div>
          <progress className="processing-progress" aria-label="概念卡生成進度" max={generation.total} value={generation.completed} />
          <p className="progress-estimate-note">已完成 {generation.completed} / {generation.total} 個觀念；依完成數計算，不代表剩餘時間。</p>
          <section className="processing-current"><h3>目前狀態</h3>
            <div className="progress-heading"><strong className="stage-label stage-status-label">{!paused && <span className="processing-status-indicator" aria-hidden="true" />}{stages[stage].label}</strong><strong>{activity}</strong></div>
            <p role="status">每個觀念依序準備來源、AI 精簡與核對來源，全部完成後開啟卡組。</p>
          </section>
        </div>
        <p className="processing-leave-note">{paused ? "這一步尚未完成，已保存的進度會保留。請重試。" : "可以離開頁面，稍後從概念卡返回查看進度。"}</p>
        {paused && <div className="processing-cancel"><button className="secondary-button" type="button" onClick={onRetry}>重試未完成部分</button></div>}
      </section>
      <ProcessingTimeline stages={stages} currentIndex={stage} activity={activity} />
    </div>
  </section>;
}

function CardStudy({ view: originalView, structure, apiClient, materialId }: { view: CardSetView; structure: KnowledgeStructureView; apiClient: StudydyApiClient; materialId?: string }) {
  const view = useMemo(() => ({ ...originalView, cards: originalView.cards.map(card => ({ ...card, display_edits: originalView.display_edits[card.concept_id] ?? [] })) }), [originalView]);
  const [order, setOrder] = useState(() => view.cards.map((_, index) => index));
  const { artworkModule, loadFailed } = useConceptArtwork();
  const loadingArtwork = !artworkModule && !loadFailed;
  const counts = useMemo(() => view.cards.map(card => artworkModule?.conceptCardPages(card, structure).length ?? 1), [artworkModule, view.cards, structure]);
  const pages = order.flatMap(index => Array.from({ length: counts[index] }, (_, pageIndex) => ({ index, pageIndex })));
  const [position, setPosition] = useState(0);
  const current = pages[position];
  const [finished, setFinished] = useState(false);
  const [notice, setNotice] = useState("");
  const stage = useRef<HTMLElement>(null);
  const move = (next: number) => {
    setNotice("");
    if (next >= pages.length) { setFinished(true); return; }
    if (next < 0) return;
    setPosition(next);
  };
  useEffect(() => {
    stage.current?.focus({ preventScroll: true });
  }, [position, finished]);
  const restart = (shuffle: boolean) => {
    const next = view.cards.map((_, index) => index);
    if (shuffle) {
      for (let i = next.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [next[i], next[j]] = [next[j], next[i]];
      }
    }
    setOrder(next);
    setPosition(0);
    setFinished(false);
    setNotice(shuffle ? "已洗牌，從第一張開始。" : "已回到第一張。");
  };
  const keys = (event: KeyboardEvent) => {
    if (loadingArtwork || finished || event.altKey || event.ctrlKey || event.metaKey || document.querySelector("dialog[open]")) return;
    const target = event.target as HTMLElement;
    // 焦點在程式碼內時保留方向鍵的原生捲動，不切換卡片。
    if (target.closest("input, textarea, select, [contenteditable=true], details, .is-code")) return;
    if (event.key === "ArrowRight") { event.preventDefault(); move(position + 1); }
    else if (event.key === "ArrowLeft") { event.preventDefault(); move(position - 1); }
  };
  return <section className="cards-page cards-study" onKeyDown={keys}>
    <header className="cards-study-heading"><div><h1>{view.name}</h1>{!materialId && <p><Icon name="book" size={15} /> {view.material_name}</p>}</div></header>
    {!view.is_current_revision && <p className="cards-notice">教材已有新版。此卡組保留建立時的教材內容與來源。</p>}
    <div className="cards-browse-layout"><nav className="cards-browse-list" aria-label="選擇概念卡">{order.map((index, positionIndex)=><button type="button" key={view.cards[index].concept_id} disabled={loadingArtwork} aria-current={current.index===index?'true':undefined} onClick={()=>{setFinished(false);move(order.slice(0, positionIndex).reduce((sum, i) => sum + counts[i], 0));}}>{view.cards[index].label}</button>)}</nav>
    <section ref={stage} tabIndex={-1} className="cards-study-stage" aria-label={finished ? "本輪瀏覽完成" : `第 ${position + 1} 張概念卡`}>
      {finished ? <div className="cards-complete"><span className="cards-empty-icon"><Icon name="check" size={36} /></span><h2>已瀏覽全部卡片</h2><p>{artworkModule ? `這一輪看過了 ${pages.length} 張概念卡。想再回顧一次嗎？` : "想再回顧一次嗎？"}</p><div className="state-actions"><button className="primary-button" type="button" onClick={() => restart(false)}>再看一次</button><button className="secondary-button" type="button" onClick={() => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" })}>返回卡組</button></div></div>
        : <><Flashcard key={`${current.index}:${current.pageIndex}`} pageIndex={current.pageIndex} card={view.cards[current.index]} structure={structure} materialName={view.material_name} apiClient={apiClient} sourceResolver={view.source_resolver} />
          <div className="cards-study-toolbar" role="group" aria-label="概念卡操作">
            <div className="cards-study-counter"><span aria-live="polite">{artworkModule ? <>第 <strong>{position + 1}</strong> / {pages.length} 張</> : loadingArtwork ? "正在計算張數…" : "張數載入失敗"}</span></div>
            <div className="cards-study-controls">
              <button className="secondary-button" type="button" disabled={loadingArtwork || position === 0} onClick={() => move(position - 1)}><Icon name="arrow-left" size={18} /> 上一張</button>
              <button className="secondary-button" type="button" disabled={loadingArtwork} onClick={() => move(position + 1)}>{position === pages.length - 1 ? "完成本輪" : "下一張"}<Icon name="chevron-right" size={18} /></button>
            </div>
            <button className="text-button cards-study-shuffle" type="button" disabled={loadingArtwork || order.length < 2} onClick={() => restart(true)}>洗牌重看</button>
          </div>
          <p className="cards-keyboard-hint">← → 切換卡片</p></>}
      {notice && <p className="cards-study-notice" role="status">{notice}</p>}
    </section></div>
  </section>;
}

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardStudyView, CardBatch } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { ProcessingTimeline } from "../material-flow/ProcessingTimeline";
import { Flashcard } from "./Flashcard";
import { useConceptArtwork } from "./VisualConceptCard";

export function CardSetPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId: string; materialId?: string }) {
  const [view, setView] = useState<CardStudyView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  // 繪圖模組與首批卡片一起載入，不等教材回應後才下載。
  useConceptArtwork();
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setError(null); setView(null);
    void (async () => {
      try {
        let result = await apiClient.getCardStudy(cardSetId);
        if (cancelled) return;
        if (!materialId) { writeRoute({ name: "card-set", cardSetId, materialId: result.material_id }, true); return; }
        if (result.material_id !== materialId) throw new Error("這個卡組不屬於此教材。");
        setView(result);
        const wasGenerating = result.generation.status !== "ready";
        while (result.generation.status !== "ready") {
          if (result.generation.status === "failed") return;
          await new Promise<void>(resolve => { timer = setTimeout(resolve, 2000); });
          if (cancelled) return;
          const generation = await apiClient.getCardGeneration(cardSetId);
          if (cancelled) return;
          result = { ...result, generation }; setView(result);
        }
        if (wasGenerating) result = await apiClient.getCardStudy(cardSetId);
        if (!cancelled) setView(result);
      } catch (failure) { if (!cancelled) setError(errorMessage(failure)); }
    })();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, cardSetId, materialId, reload]);
  return <section className="cards-page cards-study-page">
    <button className="cards-back text-button" type="button" onClick={() => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" })}><Icon name="arrow-left" size={17} /> 返回卡組</button>
    {view && view.generation.status !== "ready" && !error ? <CardGenerationProgress view={view} onRetry={() => {
      void apiClient.retryCardGeneration(cardSetId).then(() => setReload(n => n + 1)).catch(failure => setError(errorMessage(failure)));
    }} /> : error || !view?.batch ? <StateView title={error ? "無法開啟卡組" : "正在讀取概念卡"} description={error ?? "正在載入第一批卡片。"} tone={error ? "failure" : "loading"} live={!error} action={error ? <button className="primary-button" onClick={() => setReload(n => n + 1)}>重新讀取</button> : undefined} />
      : <CardStudy key={`${cardSetId}:${view.version}`} view={view} apiClient={apiClient} materialId={materialId} onReload={() => setReload(n => n + 1)} />}
  </section>;
}

function CardGenerationProgress({ view, onRetry }: { view: CardStudyView; onRetry: () => void }) {
  const generation = view.generation;
  const paused = generation.status === "failed";
  const stages = [
    { label: "準備來源", icon: "book" as const },
    { label: "整理與設計卡面", icon: "cards" as const },
    { label: "核對來源", icon: "check" as const },
    { label: "完成卡組", icon: "check" as const },
  ];
  const stage = ["preparing", "designing", "checking", "complete"].indexOf(generation.stage === "compacting" ? "designing" : generation.stage);
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
            <p role="status">每個觀念依序整理核心重點、設計圖解並核對來源；完整原文保留在詳情。</p>
            {paused && <p>{generation.error_code === "CARD_PROVIDER_CHANGED" ? "模型通道已變更，請從卡組選單重新整理卡面。" : generation.error_code === "CARD_DESIGN_REVIEW_FAILED" ? "卡面尚未通過來源與核心內容核對，可重試未完成部分。" : generation.error_code === "CARD_DESIGN_INVALID" ? "卡面文字、版型或引用未通過檢查；重試會依已保存的候選卡面與回饋修正。" : "這一步尚未完成，可重試已保存的進度。"}</p>}
          </section>
        </div>
        <p className="processing-leave-note">{paused ? "已完成的觀念會保留，重試不會從頭生成。" : "可以離開頁面，稍後從概念卡返回查看進度。"}</p>
        {paused && <div className="processing-cancel"><button className="secondary-button" type="button" onClick={onRetry}>重試未完成部分</button></div>}
      </section>
      <ProcessingTimeline stages={stages} currentIndex={stage} activity={activity} />
    </div>
  </section>;
}

function CardStudy({ view, apiClient, materialId, onReload }: { view: CardStudyView; apiClient: StudydyApiClient; materialId?: string; onReload: () => void }) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const [order, setOrder] = useState(() => view.navigation.map((_, i) => i));
  const [position, setPosition] = useState(0);
  const [pageIndex, setPageIndex] = useState(0);
  const [finished, setFinished] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [countError, setCountError] = useState(false);
  const [countRetry, setCountRetry] = useState(0);
  const [counts, setCounts] = useState<Record<string, number>>(() => Object.fromEntries(view.navigation.filter(n => n.page_count !== null).map(n => [n.concept_id, n.page_count!])));
  const savedCounts = useRef(counts);
  const [batches, setBatches] = useState(() => new Map([[0, view.batch!]]));
  const batchCache = useRef(batches);
  const pending = useRef(new Map<number, Promise<CardBatch>>());
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const index = order[position];
  const offset = Math.floor(index / 8) * 8;
  const activeOffset = useRef(offset); activeOffset.current = offset;
  const batch = batches.get(offset);
  const card = batch?.cards[index - offset];
  const layouts = useMemo(() => card && batch && artworkModule ? artworkModule.conceptCardPages(card, batch.context) : [], [card, batch, artworkModule]);
  const localCount = layouts.length || counts[view.navigation[index].concept_id] || 1;
  const localPage = pageIndex < 0 ? localCount - 1 : Math.min(pageIndex, localCount - 1);
  const total = view.navigation.every(n => counts[n.concept_id] !== undefined)
    ? view.navigation.reduce((n, entry) => n + counts[entry.concept_id], 0) : view.card_count;
  const preceding = order.slice(0, position).map(i => counts[view.navigation[i].concept_id]);
  const absolute = preceding.every(n => n !== undefined) ? preceding.reduce((sum, n) => sum + n, 0) + localPage + 1 : null;
  const stage = useRef<HTMLElement>(null);

  const ensure = useCallback((start: number): Promise<CardBatch> => {
    const cached = batchCache.current.get(start);
    if (cached) return Promise.resolve(cached);
    const running = pending.current.get(start);
    if (running) return running;
    const request = apiClient.getCardBatch(view.card_set_id, view.version, start).then(result => {
      if (result.context.source_resolver !== view.source_resolver || result.total !== view.navigation.length
        || result.cards.some((c, i) => c.concept_id !== view.navigation[start + i]?.concept_id)) throw new Error("卡片與教材版本不一致，請重新讀取卡組。");
      if (active.current) {
        const next = new Map(batchCache.current);
        next.set(start, result);
        // 只留目前與鄰近使用的三批文字；圖片另受帳號工作階段的 8 張上限保護。
        while (next.size > 3) next.delete([...next.keys()].find(key => key !== activeOffset.current)!);
        batchCache.current = next; setBatches(next);
      }
      return result;
    }).finally(() => pending.current.delete(start));
    pending.current.set(start, request);
    return request;
  }, [apiClient, view]);

  useEffect(() => {
    let cancelled = false; setError(null);
    void ensure(offset).catch(failure => { if (!cancelled) setError(errorMessage(failure)); });
    // 只預取下一批；跳選概念直接讀該批，不下載之前的卡片。
    if (offset + 8 < view.navigation.length) void ensure(offset + 8).catch(() => {});
    return () => { cancelled = true; };
  }, [offset, ensure, view.navigation.length]);

  useEffect(() => {
    if (!artworkModule) return;
    let cancelled = false;
    setCountError(false);
    void (async () => {
      for (const value of batches.values()) {
        const updates: Record<string, number> = {};
        for (const item of value.cards) {
          if (cancelled) return;
          if (savedCounts.current[item.concept_id] !== undefined) continue;
          updates[item.concept_id] = artworkModule.conceptCardPages(item, value.context).length;
          // 每個觀念間讓出主執行緒，首張與切卡不必等整批張數。
          await new Promise(resolve => setTimeout(resolve, 0));
        }
        if (cancelled || !Object.keys(updates).length) continue;
        setCounts(previous => ({ ...previous, ...updates }));
        try {
          await apiClient.saveConceptCounts(view.card_set_id, view.version, updates);
          if (!cancelled) savedCounts.current = { ...savedCounts.current, ...updates };
        } catch { if (!cancelled) setCountError(true); }
      }
    })();
    return () => { cancelled = true; };
  }, [batches, artworkModule, apiClient, view, countRetry]);

  const nextIndex = localPage + 1 < localCount ? index : order[position + 1];
  const nextBatch = nextIndex === undefined ? undefined : batches.get(Math.floor(nextIndex / 8) * 8);
  const nextCard = nextIndex === undefined ? undefined : nextBatch?.cards[nextIndex % 8];
  const nextPage = nextIndex === index ? localPage + 1 : 0;
  useEffect(() => {
    if (!artworkModule || !nextCard || !nextBatch) return;
    const timer = setTimeout(() => {
      const pages = artworkModule.conceptCardPages(nextCard, nextBatch.context);
      void artworkModule.conceptCardCanvas(nextCard, nextBatch.context, pages[nextPage], nextPage, pages.length, apiClient).catch(() => {});
    }, 0);
    return () => clearTimeout(timer);
  }, [artworkModule, nextCard, nextBatch, nextPage, apiClient]);

  const move = (direction: number) => {
    if (!card) return;
    setNotice("");
    if (direction > 0 && localPage + 1 < localCount) setPageIndex(localPage + 1);
    else if (direction < 0 && localPage > 0) setPageIndex(localPage - 1);
    else if (direction > 0 && position === order.length - 1) setFinished(true);
    else if (position + direction >= 0) { setPosition(position + direction); setPageIndex(direction < 0 ? -1 : 0); }
  };
  useEffect(() => { stage.current?.focus({ preventScroll: true }); }, [position, localPage, finished]);
  const restart = (shuffle: boolean) => {
    const next = view.navigation.map((_, i) => i);
    if (shuffle) for (let i = next.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [next[i], next[j]] = [next[j], next[i]]; }
    setOrder(next); setPosition(0); setPageIndex(0); setFinished(false);
    setNotice(shuffle ? "已洗牌，從第一張開始。" : "已回到第一張。");
  };
  const keys = (event: KeyboardEvent) => {
    if ((!artworkModule && !loadFailed) || finished || event.altKey || event.ctrlKey || event.metaKey || document.querySelector("dialog[open]")) return;
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true], details, .is-code")) return;
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
  };
  return <section className="cards-page cards-study" onKeyDown={keys}>
    {countError && <p className="form-error" role="alert">張數尚未保存。<button className="text-button" onClick={() => setCountRetry(n => n + 1)}>重試保存張數</button></p>}
    <header className="cards-study-heading"><div><h1>{view.name}</h1>{!materialId && <p><Icon name="book" size={15} /> {view.material_name}</p>}</div></header>
    {!view.is_current_revision && <p className="cards-notice">教材已有新版。此卡組保留建立時的教材內容與來源。</p>}
    <div className="cards-browse-layout"><nav className="cards-browse-list" aria-label="選擇概念卡">{order.map((i, orderIndex) => <button type="button" key={view.navigation[i].concept_id} aria-current={index === i ? 'true' : undefined} onClick={() => { setFinished(false); setPosition(orderIndex); setPageIndex(0); }}>{view.navigation[i].label}</button>)}</nav>
      <section ref={stage} tabIndex={-1} className="cards-study-stage" aria-label={finished ? "本輪瀏覽完成" : `第 ${absolute ?? localPage + 1} 張概念卡`}>
        {finished ? <div className="cards-complete"><h2>已瀏覽全部卡片</h2><p>{total !== null ? `這一輪看過了 ${total} 張概念卡。想再回顧一次嗎？` : "已瀏覽全部觀念與卡片，想再回顧一次嗎？"}</p><div className="state-actions"><button className="primary-button" onClick={() => restart(false)}>再看一次</button><button className="secondary-button" onClick={() => writeRoute(materialId ? { name: "material-content", materialId, kind: "concept-cards" } : { name: "concept-cards" })}>返回卡組</button></div></div>
          : <>{card && batch ? <Flashcard key={`${card.concept_id}:${localPage}`} card={card} pageIndex={localPage} structure={batch.context} materialName={view.material_name} apiClient={apiClient} sourceResolver={view.source_resolver} />
            : <StateView title={error ? "無法讀取這批卡片" : "正在讀取卡片"} description={error ?? "正在取得這個觀念及來源。"} tone={error ? "failure" : "loading"} action={error ? <button className="primary-button" onClick={onReload}>重新讀取卡組</button> : undefined} />}
            <div className="cards-study-toolbar" role="group" aria-label="概念卡操作">
              <div className="cards-study-counter"><span aria-live="polite">{loadFailed ? "張數載入失敗" : !card ? "正在讀取卡片…" : absolute !== null && total !== null ? <>第 <strong>{absolute}</strong> / {total} 張</> : `第 ${position + 1} 個觀念 · 第 ${localPage + 1} / ${localCount} 張`}</span></div>
              <div className="cards-study-controls">
                <button className="secondary-button" disabled={!card || (!position && !localPage)} onClick={() => move(-1)}><Icon name="arrow-left" size={18} /> 上一張</button>
                <button className="secondary-button" disabled={!card || !artworkModule && !loadFailed} onClick={() => move(1)}>{position === order.length - 1 && localPage === localCount - 1 ? "完成本輪" : "下一張"}<Icon name="chevron-right" size={18} /></button>
              </div>
              <button className="text-button cards-study-shuffle" disabled={order.length < 2} onClick={() => restart(true)}>洗牌重看</button>
            </div><p className="cards-keyboard-hint">← → 切換卡片</p></>}
        {notice && <p className="cards-study-notice" role="status">{notice}</p>}
      </section>
    </div>
  </section>;
}

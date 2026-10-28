import { openCardSetUpdate } from "../material-flow/VersionMoveNotice";
import { cardPngName } from "./card-png-name";
import { BackLink } from "../../ui/BackLink";
import { Progress } from "../../ui/Progress";
import { CardExports } from "../exports/CardExports";
import { useConceptDifficulty } from "../learning-diagnosis/LearningDiagnosis";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView } from "../../ui/NotFoundView";
import type { CardStudyView, CardBatch, CardReviewRating, CardReviewsView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { ProcessingTimeline } from "../material-flow/ProcessingTimeline";
import { CardNavFrame } from "./CardNavFrame";
import { Flashcard } from "./Flashcard";
import { CardMarkButtons, markLabel, useMarkSubmit } from "./CardMarks";
import { CardList } from "./CardList";
import { useConceptArtwork } from "./VisualConceptCard";

export function CardSetPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId: string; materialId?: string }) {
  const [view, setView] = useState<CardStudyView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reload, setReload] = useState(0);
  // 這個卡組的標記與需要加強張數；讀取失敗時只是不顯示標記，不影響瀏覽。
  const [summary, setSummary] = useState<CardReviewsView | null>(null);
  const refreshSummary = useCallback(() => {
    void apiClient.getCardReviews({ cardSetId }).then(setSummary, () => undefined);
  }, [apiClient, cardSetId]);
  useEffect(refreshSummary, [refreshSummary]);
  const markable = !!summary?.sets.some(set => set.card_set_id === cardSetId);
  // 繪圖模組與首批卡片一起載入，不等教材回應後才下載。
  useConceptArtwork();
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setError(null); setView(null); setNotFound(false);
    let loaded = false;
    void (async () => {
      try {
        let result = await apiClient.getCardStudy(cardSetId);
        if (cancelled) return;
        if (!materialId) { writeRoute({ name: "card-set", cardSetId, materialId: result.material_id }, true, window.history.state); return; }
        if (result.material_id !== materialId) { setNotFound(true); return; }
        loaded = true;
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
      } catch (failure) { if (!cancelled) { if (!loaded && isNotFound(failure)) setNotFound(true); else setError(errorMessage(failure)); } }
    })();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [apiClient, cardSetId, materialId, reload]);
  if (notFound) return <NotFoundView title="找不到這個卡組" description="這個卡組可能已被刪除，或不屬於目前登入的帳號。"
    back={materialId ? null : { label: "回到概念卡", route: { name: "concept-cards" } }} />;
  return <section className="cards-page cards-study-page">
    {!materialId && <BackLink label="返回我的概念卡" route={{ name: "concept-cards" }} />}
    {view && view.generation.status !== "ready" && !error ? <CardGenerationProgress view={view} onRetry={() => {
      void apiClient.retryCardGeneration(cardSetId).then(() => setReload(n => n + 1)).catch(failure => setError(errorMessage(failure)));
    }} /> : error || !view?.batch ? <StateView title={error ? "無法開啟卡組" : "正在讀取概念卡"} description={error ?? "正在載入第一批卡片。"} tone={error ? "failure" : "loading"} live={!error} skeleton="deck" action={error ? <button className="primary-button" onClick={() => setReload(n => n + 1)}>重新讀取</button> : undefined} />
      : <>
        {/* 已移到新版或尚未列入的卡組不提供標記與需要加強（標記由新版卡組沿用）。 */}
        <CardStudy exports={<CardExports api={apiClient} view={view} />} key={`${cardSetId}:${view.version}`} view={view} apiClient={apiClient} materialId={materialId} onReload={() => setReload(n => n + 1)}
          marks={markable ? summary?.marks : undefined} onMarked={refreshSummary}
          weak={markable && summary ? summary.forgot_count : null} />
      </>}
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
          <Progress aria-label="概念卡生成進度" max={generation.total} value={generation.completed} />
          <p className="progress-estimate-note">已完成 {generation.completed} / {generation.total} 個觀念；依完成數計算，不代表剩餘時間。</p>
          <section className="processing-current"><h3>目前狀態</h3>
            <div className="progress-heading"><strong className="stage-label stage-status-label">{!paused && <span className="processing-status-indicator" aria-hidden="true" />}{stages[stage].label}</strong><strong>{activity}</strong></div>
            <p role="status">每個觀念依序整理核心重點、設計圖解並核對來源；完整原文保留在詳情。</p>
            {paused && <p>{generation.error_code === "CARD_PROVIDER_CHANGED" ? "模型通道已變更；如需使用目前模型，請另外建立卡組。" : generation.error_code === "CARD_DESIGN_REVIEW_FAILED" ? "卡面尚未通過來源與核心內容核對，可重試未完成部分。" : generation.error_code === "CARD_DESIGN_INVALID" ? "卡面文字、版型或引用未通過檢查；重試會依已保存的候選卡面與回饋修正。" : "這一步尚未完成，可重試已保存的進度。"}</p>}
          </section>
        </div>
        <p className="processing-leave-note">{paused ? "已完成的觀念會保留，重試不會從頭生成。" : "可以離開頁面，稍後從概念卡返回查看進度。"}</p>
        {paused && <div className="processing-cancel"><button className="secondary-button" type="button" onClick={onRetry}>重試未完成部分</button></div>}
      </section>
      <ProcessingTimeline stages={stages} currentIndex={stage} activity={activity} />
    </div>
  </section>;
}

function CardStudy({ view, apiClient, materialId, onReload, marks, onMarked, weak, exports }: {
  /** 匯出整組概念卡的入口，與標題同列。 */
  exports: ReactNode;
  view: CardStudyView; apiClient: StudydyApiClient; materialId?: string; onReload: () => void;
  marks?: CardReviewsView["marks"]; onMarked: () => void;
  /** 這個卡組標成不熟的張數；null 表示不提供標記。 */
  weak: number | null;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const difficulty = useConceptDifficulty(apiClient, view.material_id, view.knowledge_structure_revision);
  const [order, setOrder] = useState(() => view.navigation.map((_, i) => i));
  const [position, setPosition] = useState(0);
  const [pageIndex, setPageIndex] = useState(0);
  const [back, setBack] = useState(false);
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
  const stage = useRef<HTMLDivElement>(null);
  // 瀏覽時直接標記目前這張卡；剛標記的結果先顯示，等重新讀取的標記回來再以伺服器為準。
  const submitMark = useMarkSubmit(apiClient);
  const [local, setLocal] = useState<Map<string, { rating: CardReviewRating; review_count: number }>>(new Map());
  const [marking, setMarking] = useState(false);
  const [markError, setMarkError] = useState<string | null>(null);
  const conceptId = view.navigation[index].concept_id;
  const saved = marks?.find(mark => mark.concept_id === conceptId);
  const pendingLocal = local.get(conceptId);
  const currentMark = pendingLocal && (!saved || pendingLocal.review_count > saved.review_count) ? pendingLocal : saved ?? null;
  const markCard = async (rating: CardReviewRating) => {
    if (marking || !marks || !back) return;
    setMarking(true); setMarkError(null);
    try {
      const result = await submitMark(view.card_set_id, conceptId, rating, currentMark?.review_count ?? 0);
      if (!active.current) return;
      setLocal(previous => new Map(previous).set(conceptId, { rating: result.rating, review_count: result.review_count }));
      setNotice(`已標成${markLabel(result.rating)}。`);
      onMarked();
    } catch (failure) {
      if (!active.current) return;
      setMarkError(errorMessage(failure));
      // 另一個分頁已標記過時，重新讀取最新標記後再標。
      onMarked();
    } finally { if (active.current) setMarking(false); }
  };

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

  useEffect(() => { setBack(false); setMarkError(null); }, [position, localPage]);

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
    setBack(false); setOrder(next); setPosition(0); setPageIndex(0); setFinished(false);
    setNotice(shuffle ? "已洗牌，從第一張開始。" : "已回到第一張。");
  };
  const keys = (event: KeyboardEvent) => {
    if ((!artworkModule && !loadFailed) || finished || event.altKey || event.ctrlKey || event.metaKey || document.querySelector("dialog[open]")) return;
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true], details, .is-code, .card-list")) return;
    if (event.key === "Enter" && event.target === stage.current) { event.preventDefault(); setBack(value => !value); }
    if (event.key === "1" || event.key === "2") { event.preventDefault(); void markCard(event.key === "1" ? "forgot" : "remembered"); }
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
  };
  const markOf = (conceptId: string) => {
    const savedMark = marks?.find(mark => mark.concept_id === conceptId), localMark = local.get(conceptId);
    return localMark && (!savedMark || localMark.review_count > savedMark.review_count) ? localMark.rating : savedMark?.rating ?? null;
  };
  const dockCount = loadFailed ? "張數載入失敗" : !card ? "讀取中…" : absolute !== null && total !== null ? <><b>{absolute}</b> / {total}</> : <><b>{position + 1}</b> / {order.length}</>;
  return <section className="cards-page cards-study" onKeyDown={keys}>
    {countError && <p className="form-error" role="alert">張數尚未保存。<button className="text-button" onClick={() => setCountRetry(n => n + 1)}>重試保存張數</button></p>}
    <header className="cards-study-heading">
      <div className="cards-study-title"><h1>{view.name}</h1>
        <span className="cards-study-meta">{total !== null && <span>{total} 張卡片</span>}{!materialId && <span><Icon name="book" size={15} /> {view.material_name}</span>}
          {/* 更新入口在卡組上：點標籤開啟只選這個卡組的更新預覽。 */}
          {!view.is_current_revision && !view.moved_to_card_set_id && <button type="button" className="cards-version-tag" onClick={() => openCardSetUpdate(view.card_set_id)}><Icon name="sparkle" size={14} />舊版內容 · 更新此卡組</button>}
          {notice && <span className="cards-study-notice" role="status">{notice}</span>}</span>
      </div>
      <div className="cards-study-actions">
        {/* 需要加強在獨立頁進行，只看這個卡組標成不熟的卡片。 */}
        {weak !== null && weak > 0 && <button className="cards-weak-link" type="button" onClick={() => writeRoute({ name: "card-review", cardSetId: view.card_set_id, materialId }, false, { fromCollection: !!window.history.state?.fromCollection })}>
          <Icon name="refresh" size={18} /><span>需要加強 <b>{weak}</b> 張</span><strong>開始加強<Icon name="chevron-right" size={16} /></strong>
        </button>}
        <button className="secondary-button cards-head-button" type="button" disabled={order.length < 2} onClick={() => restart(true)}>洗牌重看</button>
        {exports}
      </div>
    </header>
    {!view.is_current_revision && view.moved_to_card_set_id && <p className="cards-notice">這個卡組已更新，這裡保留更新前的卡面與來源；學習標記由更新後的卡組沿用。<button className="text-button" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: view.moved_to_card_set_id!, materialId: view.material_id })}>前往更新後卡組</button></p>}
    <div ref={stage} tabIndex={-1} className="cards-study-stage" aria-label={finished ? "本輪瀏覽完成" : `第 ${absolute ?? localPage + 1} 張概念卡`}>
      {finished ? <div className="cards-complete"><h2>已瀏覽全部卡片</h2><p>{total !== null ? `這一輪看過了 ${total} 張概念卡。想再回顧一次嗎？` : "已瀏覽全部觀念與卡片，想再回顧一次嗎？"}</p><div className="state-actions"><button className="primary-button" onClick={() => restart(false)}>再看一次</button></div></div>
        : <CardNavFrame onPrev={() => move(-1)} onNext={() => move(1)} prevDisabled={!card || (!position && !localPage)} nextDisabled={!card || !artworkModule && !loadFailed}
          nextLabel={position === order.length - 1 && localPage === localCount - 1 ? "完成本輪" : "下一張"}
          progress={{ value: position + 1, max: order.length, label: "瀏覽進度" }}
          list={<CardList items={order.map(i => { const mark = markOf(view.navigation[i].concept_id); return { key: view.navigation[i].concept_id, label: view.navigation[i].label, current: index === i, status: mark ? markLabel(mark) : null }; })}
            onSelect={orderIndex => { setFinished(false); setPosition(orderIndex); setPageIndex(0); }} />}
          dock={<>
            {card && marks && <CardMarkButtons current={currentMark?.rating ?? null} disabled={marking || !back} hotkeys title={back ? undefined : "先翻到背面查看重點，再標記。"} onMark={(rating) => void markCard(rating)} label={`標記「${card.label}」熟不熟`} />}
            <span className="card-dock-count" aria-live="polite">{dockCount}</span>
            {markError && <p className="form-error" role="alert">標記尚未保存。{markError}</p>}
          </>}>
          {card && batch ? <Flashcard downloadName={() => cardPngName(view, card.concept_id, localPage, apiClient)} back={back} onFlip={() => setBack(value => !value)} key={`${card.concept_id}:${localPage}`} card={card} difficulty={difficulty.data?.concepts.find(item => item.concept_id === card.concept_id)} pageIndex={localPage} structure={batch.context} materialName={view.material_name} apiClient={apiClient} sourceResolver={view.source_resolver} />
            : <StateView title={error ? "無法讀取這批卡片" : "正在讀取卡片"} description={error ?? "正在取得這個觀念及來源。"} tone={error ? "failure" : "loading"} action={error ? <button className="primary-button" onClick={onReload}>重新讀取卡組</button> : undefined} />}
        </CardNavFrame>}
    </div>
  </section>;
}

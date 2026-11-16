import { cardPngName } from "./card-png-name";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardBatch, CardReviewItem, CardReviewRating, CardStudyView, ConceptDifficultyView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { ListBreadcrumb } from "../../ui/Breadcrumb";
import { EmptyState } from "../../ui/EmptyState";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { CardMarkButtons, markLabel, useMarkSubmit } from "./CardMarks";
import { CardList } from "./CardList";
import { CardNavFrame } from "./CardNavFrame";
import { Flashcard } from "./Flashcard";
import { useConceptArtwork } from "./VisualConceptCard";
import "./card-review.css";

type LoadedCard = { study: CardStudyView; item: CardReviewItem; card: CardBatch["cards"][number]; context: CardBatch["context"]; sourceResolver: string };

/** 這一輪內重複用的讀取結果；value 讀完才有，讓看過的卡換回來時不必等一個畫面。失敗的不留，重試會重讀。 */
type CacheEntry = { promise: Promise<unknown>; value?: unknown };
type RequestCache = Map<string, CacheEntry>;
function cachedRead<T>(cache: RequestCache, key: string, load: () => Promise<T>): Promise<T> {
  const found = cache.get(key);
  if (found) return found.promise as Promise<T>;
  const entry: CacheEntry = { promise: load() };
  cache.set(key, entry);
  entry.promise.then(value => { entry.value = value; }, () => { if (cache.get(key) === entry) cache.delete(key); });
  return entry.promise as Promise<T>;
}
const peek = <T,>(cache: RequestCache, key: string) => cache.get(key)?.value as T | undefined;
const studyKey = (item: CardReviewItem) => `study:${item.card_set_id}:${item.knowledge_structure_revision}`;
const batchKey = (item: CardReviewItem, version: number, offset: number) => `batch:${item.card_set_id}:${version}:${offset}`;
const difficultyKey = (item: CardReviewItem) => `difficulty:${item.material_id}:${item.knowledge_structure_revision}`;

/** 組出這張卡；卡片與卡組版本對不上時丟錯。 */
function assemble(study: CardStudyView, batch: CardBatch, item: CardReviewItem, index: number, offset: number): LoadedCard {
  const card = batch.cards[index - offset];
  if (card?.concept_id !== item.card.concept_id || batch.context.source_resolver !== study.source_resolver) throw new Error("卡片與卡組版本不一致，請重新讀取。");
  return { study, item, card, context: batch.context, sourceResolver: study.source_resolver };
}
function locate(study: CardStudyView, item: CardReviewItem) {
  const index = study.navigation.findIndex((entry) => entry.concept_id === item.card.concept_id);
  if (index < 0 || study.knowledge_structure_revision !== item.knowledge_structure_revision) throw new Error("卡片與卡組版本不一致，請重新讀取。");
  return { index, offset: Math.floor(index / 8) * 8 };
}

/**
 * 需要加強：一次一張、一輪看完就停。卡片與卡組瀏覽時是同一張概念卡（圖卡、完整重點與來源頁碼），
 * 卡片內容由卡組的瀏覽 API 讀取（需要加強清單只給卡片重點，沒有圖卡需要的教材關係）。
 */
export function CardReviewPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId?: string; materialId?: string }) {
  // 只篩教材時在教材內：教材側欄的「翻卡片」已能回到這份教材的卡組，不再另放返回。
  const materialOnly = !cardSetId && !!materialId;
  // 全站版面左上的「×」回到全站翻卡片；教材內改放麵包屑（教材側欄也能回到清單）。
  const closeButton = materialId ? null : <button className="review-close" type="button" aria-label="回到翻卡片" title="回到翻卡片" onClick={() => writeRoute({ name: "concept-cards" })}><Icon name="close" size={22} /></button>;
  // 教材內叫「不熟的卡」，全站版仍叫「需要加強」。
  const title = materialId ? "不熟的卡" : "需要加強";
  const crumb = materialId ? <ListBreadcrumb kind="concept-cards" materialId={materialId} current={title} /> : null;
  // 這一輪的清單固定；已處理項目可以回看，但不重複送出標記。
  const [round, setRound] = useState<CardReviewItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [position, setPosition] = useState(0);
  const [enterLastPage, setEnterLastPage] = useState(false);
  const [processed, setProcessed] = useState<Map<number, CardReviewRating | null>>(new Map());
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const cache = useRef<RequestCache>(new Map()).current;
  useEffect(() => {
    let cancelled = false;
    setRound(null); setLoadError(null); setPosition(0); setEnterLastPage(false); setProcessed(new Map()); setNotice(null);
    void apiClient.getCardReviews({ cardSetId, materialId: materialOnly ? materialId : undefined }).then(
      (value) => { if (!cancelled) setRound(value.cards); },
      (failure) => { if (!cancelled) setLoadError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, cardSetId, materialId, materialOnly, reload]);
  const setName = round?.[0]?.card_set_name;
  const description = cardSetId ? setName ? `卡組「${setName}」中標成不熟的卡片` : "這個卡組標成不熟的卡片" : materialOnly ? "這份教材各卡組中標成不熟的卡片" : "所有卡組中標成不熟的卡片";
  // 上方列：關閉、標題與說明、每張卡一格的進度（已處理的填色）和第幾張。
  const heading = (origin?: string | null, progress?: { done: Set<number>; position: number; total: number }) => <>{crumb}<header className="card-review-heading">
    {closeButton}
    <div className="cards-study-title">
      <h1>{title}</h1>
      <span className="cards-study-meta"><span>{description}</span>{origin && <span title={origin}><Icon name="cards" size={15} />{origin}</span>}{notice && <span className="cards-study-notice" role="status">{notice}</span>}</span>
    </div>
    {progress && <>
      <div className="review-dots" aria-hidden="true">{Array.from({ length: progress.total }, (_, i) => <i key={i} className={i === progress.position ? "is-current" : progress.done.has(i) ? "is-done" : undefined} />)}</div>
      <span className="review-count">第 {progress.position + 1} / {progress.total} 張</span>
    </>}
  </header></>;
  if (loadError) return <section className="card-review-page">
    {heading()}
    <StateView title={`無法讀取${title}的卡片`} description={loadError} tone="failure"
      action={<button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>重新讀取</button>} />
  </section>;
  if (!round) return <section className="card-review-page">
    {heading()}
    <StateView title={`正在讀取${title}的卡片`} description="正在整理這一輪要看的卡片。" tone="loading" live skeleton="deck" />
  </section>;
  const done = processed.size === round.length;
  const marked = { forgot: [...processed.values()].filter(value => value === "forgot").length, remembered: [...processed.values()].filter(value => value === "remembered").length };
  return <section className="card-review-page">
    {heading(
    // 混合多個卡組時標示這張卡的來源（教材內只寫卡組，全站另附教材）。
    round.length > 0 && !done && !cardSetId ? materialOnly ? round[position].card_set_name : `${round[position].card_set_name} · ${round[position].material_name}` : null,
    round.length > 0 && !done ? { done: new Set(processed.keys()), position, total: round.length } : undefined)}
    {round.length === 0
      ? <EmptyState label={`${title}空狀態`} title={`目前沒有${title}的卡片`} description="瀏覽卡組時，在卡片下方標成「不熟」，就會出現在這裡。" />
      : done
        ? <div className="card-review-done surface" role="status">
          <img src="/assets/studydy/success-jump.png" alt="" />
          <h2>這一輪加強完成！</h2>
          <p>標成熟悉 <b>{marked.remembered}</b> 張{marked.forgot > 0 && <>，仍不熟 <b>{marked.forgot}</b> 張，下次會再出現在{title}</>}。</p>
          <div className="state-actions">
            {marked.forgot > 0 && <button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>再加強一輪</button>}
          </div>
        </div>
        : <ReviewCard title={title} cache={cache} apiClient={apiClient} item={round[position]} onBusy={setSaving} processed={processed.has(position)} processedCount={processed.size} savedRating={processed.get(position) ?? null} position={position} total={round.length} enterLastPage={enterLastPage} onMove={direction => { setEnterLastPage(direction < 0); setPosition(value => value + direction); }}
          list={<CardList disabled={saving} items={round.map((entry, i) => ({ key: `${entry.card_set_id}:${entry.card.concept_id}`, label: entry.card.label, current: position === i, status: processed.has(i) ? "已處理" : null }))}
            onSelect={i => { setEnterLastPage(false); setPosition(i); }} />}
          onMarked={(rating) => {
            const next = new Map(processed).set(position, rating);
            setProcessed(next); setEnterLastPage(false);
            const nextPosition = round.findIndex((_, i) => i > position && !next.has(i));
            const remaining = nextPosition >= 0 ? nextPosition : round.findIndex((_, i) => !next.has(i));
            if (remaining >= 0) setPosition(remaining);
            if (!rating) { setNotice("上一張卡已在其他頁面標記過，這裡不重複標記。"); return; }
            setNotice(rating === "remembered" ? `已標成熟悉，離開${title}。` : "已標成不熟，下一輪還會出現。");
            // 標記後重新讀取學習活動，讓升級動畫與今日任務跟上（失敗時保留原畫面）。
            void apiClient.getLearningActivity().catch(() => undefined);
          }} />}
  </section>;
}

function ReviewCard({ title, cache, apiClient, item, list, onMarked, processed, processedCount, savedRating, position, total, onMove, onBusy, enterLastPage }: {
  title: string; cache: RequestCache; apiClient: StudydyApiClient; item: CardReviewItem;
  enterLastPage: boolean; list: ReactNode;
  onBusy: (busy: boolean) => void;
  processed: boolean; processedCount: number; savedRating: CardReviewRating | null; position: number; total: number; onMove: (direction: number) => void;
  /** 標記完成後換下一張；null 表示這張已在其他頁面標記過，不重複計次。 */
  onMarked: (rating: CardReviewRating | null) => void;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  // ReviewCard 換卡時不重建（外框、清單、按鈕、標記列都留著），每張卡自己的狀態在 cardKey 變動時手動重設。
  const cardKey = `${item.card_set_id}:${item.card.concept_id}`;
  const [shownKey, setShownKey] = useState(cardKey);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const [filled, setFilled] = useState(0);
  const [pageIndex, setPageIndex] = useState(enterLastPage ? -1 : 0);
  const [busy, setBusy] = useState(false);
  const [back, setBack] = useState(false);
  const [markError, setMarkError] = useState<string | null>(null);
  if (shownKey !== cardKey) {
    // 新卡一律從正面開始；從下一張按「上一張」回來時停在末頁。
    setShownKey(cardKey); setFailure(null); setRetry(0); setPageIndex(enterLastPage ? -1 : 0); setBack(false); setMarkError(null);
  }
  const stage = useRef<HTMLElement>(null);
  const failed = useRef<CardReviewRating | null>(null);
  // 換卡後焦點回到卡面，報讀才會讀到新卡名稱。
  useEffect(() => { failed.current = null; stage.current?.focus({ preventScroll: true }); }, [cardKey]);
  const submit = useMarkSubmit(apiClient);

  // 卡面資料依「卡組＋版本」、分批依「卡組＋版本＋位移」快取；已讀過的同步組出，不經過讀取畫面。
  const loaded = useMemo<LoadedCard | null>(() => {
    const study = peek<CardStudyView>(cache, studyKey(item));
    if (!study) return null;
    try {
      const { index, offset } = locate(study, item);
      const batch = peek<CardBatch>(cache, batchKey(item, study.version, offset));
      return batch ? assemble(study, batch, item, index, offset) : null;
    } catch { return null; }
    // filled：cache 有新結果時重算（cache 本身是 ref，不會觸發重繪）。
  }, [cache, item, filled]);
  useEffect(() => {
    if (loaded) return;
    let cancelled = false;
    void (async () => {
      const study = await cachedRead(cache, studyKey(item), () => apiClient.getCardStudy(item.card_set_id));
      const { index, offset } = locate(study, item);
      const batch = await cachedRead(cache, batchKey(item, study.version, offset),
        () => offset === 0 && study.batch ? Promise.resolve(study.batch) : apiClient.getCardBatch(item.card_set_id, study.version, offset));
      assemble(study, batch, item, index, offset);
      if (!cancelled) setFilled(value => value + 1);
    })().catch((reason) => { if (!cancelled) setFailure({ key: cardKey, message: errorMessage(reason) }); });
    return () => { cancelled = true; };
  }, [apiClient, cache, item, cardKey, loaded, retry]);
  const error = failure?.key === cardKey ? failure.message : null;
  const difficulty = peek<ConceptDifficultyView>(cache, difficultyKey(item));
  useEffect(() => {
    let cancelled = false;
    // 難度資料讀不到就當作沒有，不擋卡片。
    cachedRead(cache, difficultyKey(item), () => apiClient.getConceptDifficulty({ materialId: item.material_id, structureRevision: item.knowledge_structure_revision }))
      .then(() => { if (!cancelled) setFilled(value => value + 1); }, () => undefined);
    return () => { cancelled = true; };
  }, [apiClient, cache, item.material_id, item.knowledge_structure_revision]);
  const pages = useMemo(() => loaded && artworkModule ? artworkModule.conceptCardPages(loaded.card, loaded.context).length : 1, [loaded, artworkModule]);
  const localPage = pageIndex < 0 ? pages - 1 : pageIndex;
  // -1 表示末頁；真正頁數載入後的 localPage 變動不算使用者換頁，不撤銷翻面。
  useEffect(() => { setBack(false); }, [pageIndex]);
  const mark = useCallback(async (rating: CardReviewRating) => {
    if (busy || processed || !back) return;
    setBusy(true); onBusy(true); setMarkError(null);
    try {
      await submit(item.card_set_id, item.card.concept_id, rating, item.review_count);
      failed.current = null;
      onMarked(rating);
    } catch (failure) {
      // 另一個分頁已標過這張卡時，直接跳到下一張，不重複計次。
      if (failure instanceof ApiClientError && failure.reasonCode === "CARD_REVIEW_CONFLICT") { failed.current = null; onMarked(null); return; }
      failed.current = rating;
      setMarkError(errorMessage(failure));
    } finally { setBusy(false); onBusy(false); }
  }, [busy, processed, back, submit, item, onMarked, onBusy]);
  const move = (direction: number) => {
    if (busy) return;
    if (direction > 0 && localPage + 1 < pages) setPageIndex(localPage + 1);
    else if (direction < 0 && localPage > 0) setPageIndex(localPage - 1);
    else if (position + direction >= 0 && position + direction < total) onMove(direction);
  };
  const keys = (event: KeyboardEvent) => {
    if (event.altKey || event.ctrlKey || event.metaKey || document.querySelector("dialog[open]") || (event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true], details, .is-code, .card-list")) return;
    if (event.key === "Enter" && event.target === stage.current) { event.preventDefault(); setBack(value => !value); }
    if (event.key === "1" || event.key === "2") { event.preventDefault(); if (loaded) void mark(event.key === "1" ? "forgot" : "remembered"); }
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
  };
  const status = processed ? savedRating ? `本輪已標成${markLabel(savedRating)}，回看不會重複記錄。` : "這張卡已在其他頁面標記，本輪只供回看。" : busy ? "正在保存…" : null;
  return <article ref={stage} tabIndex={-1} onKeyDown={keys} className="card-review-stage cards-study-stage" aria-label={`${title}：${item.card.label}`}>
    <CardNavFrame list={list}
      aside={<><p>翻到背面看過，才能標記熟不熟。</p><img src="/assets/studydy/brand-idle.png" alt="" /></>}
      onPrev={() => move(-1)} onNext={() => move(1)} prevDisabled={busy || !loaded || position === 0 && localPage === 0} nextDisabled={busy || !loaded || position === total - 1 && localPage === pages - 1}
      progress={{ value: processedCount, max: total, label: "已看完的張數" }}
      dock={<>
        <CardMarkButtons current={savedRating} disabled={busy || !loaded || !back || processed} hotkeys title={back ? undefined : "先翻到背面查看重點，再標記。"} onMark={(rating) => void mark(rating)} label={`標記「${item.card.label}」熟不熟`} />
        <span className="card-dock-hint keyboard-hint">← → 換卡 · 看過背面後按 1 或 2</span>
        <span className="card-dock-count"><b>{position + 1}</b> / {total}{pages > 1 && !loadFailed && <small> · 卡面 {localPage + 1} / {pages}</small>}</span>
      </>}>
    {loaded
      ? <Flashcard downloadName={() => cardPngName(loaded.study, loaded.card.concept_id, localPage, apiClient)} back={back} onFlip={() => setBack(value => !value)} key={`${cardKey}:${localPage}`} card={loaded.card} difficulty={difficulty?.concepts.find(concept => concept.concept_id === loaded.card.concept_id)} structure={loaded.context} pageIndex={localPage} materialName={item.material_name} apiClient={apiClient} sourceResolver={loaded.sourceResolver} />
      // 讀取中維持卡面原本的大小，上下張按鈕和標記列不會跟著跳。
      : <div className="card-face-pending"><StateView title={error ? "無法讀取這張卡片" : "正在讀取卡片"} description={error ?? "正在取得卡面與來源。"} tone={error ? "failure" : "loading"} live={!error}
        action={error ? <button className="primary-button" type="button" onClick={() => { setFailure(null); setRetry((value) => value + 1); }}>重新讀取</button> : undefined} /></div>}
    {(status || markError) && <p className="card-face-chip" role="status">{status}{markError && <span className="form-error" role="alert">標記尚未保存。{markError}<button className="text-button" type="button" onClick={() => void mark(failed.current!)}>重試</button></span>}</p>}
    </CardNavFrame>
  </article>;
}

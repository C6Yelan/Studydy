import { cardPngName } from "./card-png-name";
import { useConceptDifficulty } from "../learning-diagnosis/LearningDiagnosis";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardBatch, CardReviewItem, CardReviewRating, CardStudyView } from "../../api/contracts";
import { type AppRoute } from "../../app/routes";
import { BackLink } from "../../ui/BackLink";
import { EmptyState } from "../../ui/EmptyState";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { CardMarkButtons, markLabel, useMarkSubmit } from "./CardMarks";
import { CardListMenu } from "./CardListMenu";
import { CardNavFrame } from "./CardNavFrame";
import { Flashcard } from "./Flashcard";
import { useConceptArtwork } from "./VisualConceptCard";
import "./card-review.css";

type LoadedCard = { study: CardStudyView; item: CardReviewItem; card: CardBatch["cards"][number]; context: CardBatch["context"]; sourceResolver: string };

/**
 * 需要加強：一次一張、一輪看完就停。卡片與卡組瀏覽時是同一張概念卡（圖卡、完整重點與來源頁碼），
 * 卡片內容由卡組的瀏覽 API 讀取（需要加強清單只給卡片重點，沒有圖卡需要的教材關係）。
 */
export function CardReviewPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId?: string; materialId?: string }) {
  // 只篩教材時在教材內：教材頁首的「概念卡」分頁已能回到這份教材的卡組，頁首不再另放返回。
  const materialOnly = !cardSetId && !!materialId;
  const back: { label: string; route: AppRoute } = materialId && !window.history.state?.fromCollection
    ? { label: "返回概念卡", route: { name: "material-content", materialId, kind: "concept-cards" } }
    : { label: "返回概念卡", route: { name: "concept-cards" } };
  const backLink = materialId ? null : <BackLink {...back} />;
  // 這一輪的清單固定；已處理項目可以回看，但不重複送出標記。
  const [round, setRound] = useState<CardReviewItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [position, setPosition] = useState(0);
  const [enterLastPage, setEnterLastPage] = useState(false);
  const [processed, setProcessed] = useState<Map<number, CardReviewRating | null>>(new Map());
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
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
  const heading = (actions?: ReactNode, origin?: string | null) => <header className="cards-study-heading card-review-heading">
    <div className="cards-study-title">
      <h1>需要加強</h1>
      <span className="cards-study-meta"><span>{description}</span>{origin && <span title={origin}><Icon name="cards" size={15} />{origin}</span>}{notice && <span className="cards-study-notice" role="status">{notice}</span>}</span>
    </div>
    {actions && <div className="cards-study-actions">{actions}</div>}
  </header>;
  if (loadError) return <section className="card-review-page">
    {backLink}
    {heading()}
    <StateView title="無法讀取需要加強的卡片" description={loadError} tone="failure"
      action={<button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>重新讀取</button>} />
  </section>;
  if (!round) return <section className="card-review-page">
    {backLink}
    {heading()}
    <StateView title="正在讀取需要加強的卡片" description="正在整理這一輪要看的卡片。" tone="loading" live skeleton="deck" />
  </section>;
  const done = processed.size === round.length;
  const marked = { forgot: [...processed.values()].filter(value => value === "forgot").length, remembered: [...processed.values()].filter(value => value === "remembered").length };
  return <section className="card-review-page">
    {backLink}
    {heading(round.length > 0 && !done && <CardListMenu disabled={saving}
      items={round.map((item, i) => ({ key: `${item.card_set_id}:${item.card.concept_id}`, label: item.card.label, current: position === i, status: processed.has(i) ? "已處理" : null }))}
      onSelect={i => { setEnterLastPage(false); setPosition(i); }} />,
    // 混合多個卡組時標示這張卡的來源（教材內只寫卡組，全站另附教材）。
    round.length > 0 && !done && !cardSetId ? materialOnly ? round[position].card_set_name : `${round[position].card_set_name} · ${round[position].material_name}` : null)}
    {round.length === 0
      ? <EmptyState label="需要加強空狀態" title="目前沒有需要加強的卡片" description="瀏覽卡組時，在卡片下方標成「不熟」，就會出現在這裡。" />
      : done
        ? <div className="card-review-done surface" role="status">
          <img src="/assets/studydy/success-jump.png" alt="" />
          <h2>這一輪加強完成！</h2>
          <p>標成熟悉 <b>{marked.remembered}</b> 張{marked.forgot > 0 && <>，仍不熟 <b>{marked.forgot}</b> 張，下次會再出現在需要加強</>}。</p>
          <div className="state-actions">
            {marked.forgot > 0 && <button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>再加強一輪</button>}
          </div>
        </div>
        : <ReviewCard key={`${round[position].card_set_id}:${round[position].card.concept_id}`} apiClient={apiClient} item={round[position]} onBusy={setSaving} processed={processed.has(position)} processedCount={processed.size} savedRating={processed.get(position) ?? null} position={position} total={round.length} enterLastPage={enterLastPage} onMove={direction => { setEnterLastPage(direction < 0); setPosition(value => value + direction); }}
          onMarked={(rating) => {
            const next = new Map(processed).set(position, rating);
            setProcessed(next); setEnterLastPage(false);
            const nextPosition = round.findIndex((_, i) => i > position && !next.has(i));
            const remaining = nextPosition >= 0 ? nextPosition : round.findIndex((_, i) => !next.has(i));
            if (remaining >= 0) setPosition(remaining);
            if (!rating) { setNotice("上一張卡已在其他頁面標記過，這裡不重複標記。"); return; }
            setNotice(rating === "remembered" ? "已標成熟悉，離開需要加強。" : "已標成不熟，下一輪還會出現。");
            // 標記後重新讀取學習活動，讓頁首經驗值與今日任務跟上（失敗時保留原畫面）。
            void apiClient.getLearningActivity().catch(() => undefined);
          }} />}
  </section>;
}

function ReviewCard({ apiClient, item, onMarked, processed, processedCount, savedRating, position, total, onMove, onBusy, enterLastPage }: {
  apiClient: StudydyApiClient; item: CardReviewItem;
  enterLastPage: boolean;
  onBusy: (busy: boolean) => void;
  processed: boolean; processedCount: number; savedRating: CardReviewRating | null; position: number; total: number; onMove: (direction: number) => void;
  /** 標記完成後換下一張；null 表示這張已在其他頁面標記過，不重複計次。 */
  onMarked: (rating: CardReviewRating | null) => void;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const difficulty = useConceptDifficulty(apiClient, item.material_id, item.knowledge_structure_revision);
  const [loaded, setLoaded] = useState<LoadedCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [pageIndex, setPageIndex] = useState(enterLastPage ? -1 : 0);
  const [busy, setBusy] = useState(false);
  const [back, setBack] = useState(false);
  const stage = useRef<HTMLElement>(null);
  useEffect(() => { stage.current?.focus({ preventScroll: true }); }, []);

  const [markError, setMarkError] = useState<string | null>(null);
  const failed = useRef<CardReviewRating | null>(null);
  const submit = useMarkSubmit(apiClient);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void (async () => {
      const study: CardStudyView = await apiClient.getCardStudy(item.card_set_id);
      const index = study.navigation.findIndex((entry) => entry.concept_id === item.card.concept_id);
      if (index < 0 || study.knowledge_structure_revision !== item.knowledge_structure_revision) throw new Error("卡片與卡組版本不一致，請重新讀取。");
      const offset = Math.floor(index / 8) * 8;
      const batch = offset === 0 && study.batch ? study.batch : await apiClient.getCardBatch(item.card_set_id, study.version, offset);
      const card = batch.cards[index - offset];
      if (card?.concept_id !== item.card.concept_id || batch.context.source_resolver !== study.source_resolver) throw new Error("卡片與卡組版本不一致，請重新讀取。");
      if (!cancelled) setLoaded({ study, item, card, context: batch.context, sourceResolver: study.source_resolver });
    })().catch((failure) => { if (!cancelled) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, item, retry]);
  const pages = useMemo(() => loaded && artworkModule ? artworkModule.conceptCardPages(loaded.card, loaded.context).length : 1, [loaded, artworkModule]);
  const localPage = pageIndex < 0 ? pages - 1 : pageIndex;
  useEffect(() => { setBack(false); }, [localPage]);
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
    if (event.altKey || event.ctrlKey || event.metaKey || document.querySelector("dialog[open]") || (event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true], details, .is-code, .card-list-menu")) return;
    if (event.key === "Enter" && event.target === stage.current) { event.preventDefault(); setBack(value => !value); }
    if (event.key === "1" || event.key === "2") { event.preventDefault(); if (loaded) void mark(event.key === "1" ? "forgot" : "remembered"); }
    if (event.key === "ArrowRight") { event.preventDefault(); move(1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); move(-1); }
  };
  const status = processed ? savedRating ? `本輪已標成${markLabel(savedRating)}，回看不會重複記錄。` : "這張卡已在其他頁面標記，本輪只供回看。" : busy ? "正在保存…" : null;
  return <article ref={stage} tabIndex={-1} onKeyDown={keys} className="card-review-stage cards-study-stage" aria-label={`需要加強：${item.card.label}`}>
    <CardNavFrame onPrev={() => move(-1)} onNext={() => move(1)} prevDisabled={busy || !loaded || position === 0 && localPage === 0} nextDisabled={busy || !loaded || position === total - 1 && localPage === pages - 1}
      progress={{ value: processedCount, max: total, label: "已看完的張數" }}
      dock={<>
        <CardMarkButtons current={savedRating} disabled={busy || !loaded || !back || processed} hotkeys title={back ? undefined : "先翻到背面查看重點，再標記。"} onMark={(rating) => void mark(rating)} label={`標記「${item.card.label}」熟不熟`} />
        <span className="card-dock-count"><b>{position + 1}</b> / {total}{pages > 1 && !loadFailed && <small> · 卡面 {localPage + 1} / {pages}</small>}</span>
      </>}>
    {loaded
      ? <Flashcard downloadName={() => cardPngName(loaded.study, loaded.card.concept_id, localPage, apiClient)} back={back} onFlip={() => setBack(value => !value)} key={localPage} card={loaded.card} difficulty={difficulty.data?.concepts.find(concept => concept.concept_id === loaded.card.concept_id)} structure={loaded.context} pageIndex={localPage} materialName={item.material_name} apiClient={apiClient} sourceResolver={loaded.sourceResolver} />
      : <StateView title={error ? "無法讀取這張卡片" : "正在讀取卡片"} description={error ?? "正在取得卡面與來源。"} tone={error ? "failure" : "loading"} live={!error}
        action={error ? <button className="primary-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新讀取</button> : undefined} />}
    {(status || markError) && <p className="card-face-chip" role="status">{status}{markError && <span className="form-error" role="alert">標記尚未保存。{markError}<button className="text-button" type="button" onClick={() => void mark(failed.current!)}>重試</button></span>}</p>}
    </CardNavFrame>
  </article>;
}

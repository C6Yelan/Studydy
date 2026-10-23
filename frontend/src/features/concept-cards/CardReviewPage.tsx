import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardBatch, CardReviewItem, CardReviewRating, CardStudyView } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { BackLink } from "../../ui/BackLink";
import { EmptyState } from "../../ui/EmptyState";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { CardMarkButtons, useMarkSubmit } from "./CardMarks";
import { Flashcard } from "./Flashcard";
import { useConceptArtwork } from "./VisualConceptCard";
import "./card-review.css";

type LoadedCard = { item: CardReviewItem; card: CardBatch["cards"][number]; context: CardBatch["context"]; sourceResolver: string };

/**
 * 需要加強：一次一張、一輪看完就停。卡片與卡組瀏覽時是同一張概念卡（圖卡、完整重點與來源頁碼），
 * 卡片內容由卡組的瀏覽 API 讀取（需要加強清單只給卡片重點，沒有圖卡需要的教材關係）。
 */
export function CardReviewPage({ apiClient, cardSetId, materialId }: { apiClient: StudydyApiClient; cardSetId?: string; materialId?: string }) {
  // 只篩教材時在教材內：教材頁首的「概念卡」分頁已能回到這份教材的卡組，頁首不再另放返回。
  const materialOnly = !cardSetId && !!materialId;
  const back: { label: string; route: AppRoute } = cardSetId
    ? { label: "返回卡組", route: { name: "card-set", cardSetId, materialId } }
    : materialId ? { label: "返回概念卡", route: { name: "material-content", materialId, kind: "concept-cards" } }
    : { label: "返回概念卡", route: { name: "concept-cards" } };
  const backLink = materialOnly ? null : <BackLink {...back} />;
  // 這一輪的清單在開始時固定；標記後只往下一張，不重新排入同一批卡。
  const [round, setRound] = useState<CardReviewItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [position, setPosition] = useState(0);
  const [marked, setMarked] = useState<{ forgot: number; remembered: number }>({ forgot: 0, remembered: 0 });
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    setRound(null); setLoadError(null); setPosition(0); setMarked({ forgot: 0, remembered: 0 }); setNotice(null);
    void apiClient.getCardReviews({ cardSetId, materialId: materialOnly ? materialId : undefined }).then(
      (value) => { if (!cancelled) setRound(value.cards); },
      (failure) => { if (!cancelled) setLoadError(errorMessage(failure)); },
    );
    return () => { cancelled = true; };
  }, [apiClient, cardSetId, materialId, materialOnly, reload]);
  const setName = round?.[0]?.card_set_name;
  const heading = <header className="card-review-heading">
    <div>
      <h1>需要加強</h1>
      <p>{cardSetId ? setName ? `卡組「${setName}」中標成不熟的卡片` : "這個卡組標成不熟的卡片" : materialOnly ? "這份教材各卡組中標成不熟的卡片" : "所有卡組中標成不熟的卡片"} · 看完卡片，再標記熟不熟。</p>
    </div>
  </header>;
  if (loadError) return <section className="card-review-page">
    {backLink}
    {heading}
    <StateView title="無法讀取需要加強的卡片" description={loadError} tone="failure"
      action={<button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>重新讀取</button>} />
  </section>;
  if (!round) return <section className="card-review-page">
    {backLink}
    {heading}
    <StateView title="正在讀取需要加強的卡片" description="正在整理這一輪要看的卡片。" tone="loading" live skeleton="deck" />
  </section>;
  const done = position >= round.length;
  return <section className="card-review-page">
    {backLink}
    {heading}
    {round.length > 0 && <div className="card-review-progress" aria-label="這一輪進度">
      <span>{done ? <>這一輪完成</> : <>第 <b>{position + 1}</b> / {round.length} 張</>}</span>
      <progress max={round.length} value={done ? round.length : position} aria-label="已看完的張數" />
    </div>}
    {notice && <p className="card-review-notice" role="status">{notice}</p>}
    {round.length === 0
      ? <EmptyState label="需要加強空狀態" title="目前沒有需要加強的卡片" description="瀏覽卡組時，在卡片下方標成「不熟」，就會出現在這裡。"
        action={<button className="primary-button" type="button" onClick={() => writeRoute(back.route)}>{back.label}</button>} />
      : done
        ? <div className="card-review-done surface" role="status">
          <img src="/assets/studydy/success-jump.png" alt="" />
          <h2>這一輪加強完成！</h2>
          <p>標成熟悉 <b>{marked.remembered}</b> 張{marked.forgot > 0 && <>，仍不熟 <b>{marked.forgot}</b> 張，下次會再出現在需要加強</>}。</p>
          <div className="state-actions">
            {marked.forgot > 0 && <button className="primary-button" type="button" onClick={() => setReload((value) => value + 1)}>再加強一輪</button>}
            <button className={marked.forgot > 0 ? "secondary-button" : "primary-button"} type="button" onClick={() => writeRoute(back.route)}>{back.label}</button>
          </div>
        </div>
        : <ReviewCard key={`${round[position].card_set_id}:${round[position].card.concept_id}`} apiClient={apiClient} item={round[position]}
          source={cardSetId ? null : materialOnly ? item => item.card_set_name : item => `${item.card_set_name} · ${item.material_name}`}
          onMarked={(rating) => {
            setPosition((value) => value + 1);
            if (!rating) { setNotice("上一張卡已在其他頁面標記過，這裡不重複標記。"); return; }
            setNotice(rating === "remembered" ? "已標成熟悉，離開需要加強。" : "已標成不熟，下一輪還會出現。");
            setMarked((value) => ({ ...value, [rating]: value[rating] + 1 }));
            // 標記後重新讀取學習活動，讓頁首經驗值與今日任務跟上（失敗時保留原畫面）。
            void apiClient.getLearningActivity().catch(() => undefined);
          }} />}
  </section>;
}

function ReviewCard({ apiClient, item, source, onMarked }: {
  apiClient: StudydyApiClient; item: CardReviewItem;
  /** 混合多個卡組時標示這張卡的來源（教材內只寫卡組，全站另附教材）。 */
  source: ((item: CardReviewItem) => string) | null;
  /** 標記完成後換下一張；null 表示這張已在其他頁面標記過，不重複計次。 */
  onMarked: (rating: CardReviewRating | null) => void;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const [loaded, setLoaded] = useState<LoadedCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [pageIndex, setPageIndex] = useState(0);
  const [busy, setBusy] = useState(false);
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
      if (!cancelled) setLoaded({ item, card, context: batch.context, sourceResolver: study.source_resolver });
    })().catch((failure) => { if (!cancelled) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, item, retry]);
  const pages = useMemo(() => loaded && artworkModule ? artworkModule.conceptCardPages(loaded.card, loaded.context).length : 1, [loaded, artworkModule]);
  const mark = useCallback(async (rating: CardReviewRating) => {
    if (busy) return;
    setBusy(true); setMarkError(null);
    try {
      await submit(item.card_set_id, item.card.concept_id, rating, item.review_count);
      failed.current = null;
      onMarked(rating);
    } catch (failure) {
      // 另一個分頁已標過這張卡時，直接跳到下一張，不重複計次。
      if (failure instanceof ApiClientError && failure.reasonCode === "CARD_REVIEW_CONFLICT") { failed.current = null; onMarked(null); return; }
      failed.current = rating;
      setMarkError(errorMessage(failure));
    } finally { setBusy(false); }
  }, [busy, submit, item, onMarked]);
  return <article className="card-review-stage" aria-label={`需要加強：${item.card.label}`}>
    {source && <p className="card-review-source" title={source(item)}><Icon name="cards" size={15} /><span>{source(item)}</span></p>}
    {loaded
      ? <Flashcard key={pageIndex} card={loaded.card} structure={loaded.context} pageIndex={pageIndex} materialName={item.material_name} apiClient={apiClient} sourceResolver={loaded.sourceResolver} />
      : <StateView title={error ? "無法讀取這張卡片" : "正在讀取卡片"} description={error ?? "正在取得卡面與來源。"} tone={error ? "failure" : "loading"} live={!error}
        action={error ? <button className="primary-button" type="button" onClick={() => setRetry((value) => value + 1)}>重新讀取</button> : undefined} />}
    {loaded && pages > 1 && !loadFailed && <div className="card-review-pages" role="group" aria-label="這個概念的卡面">
      <button className="secondary-button" type="button" disabled={pageIndex === 0} onClick={() => setPageIndex((value) => value - 1)}><Icon name="arrow-left" size={16} />上一面</button>
      <span>卡面 {pageIndex + 1} / {pages}</span>
      <button className="secondary-button" type="button" disabled={pageIndex >= pages - 1} onClick={() => setPageIndex((value) => value + 1)}>下一面<Icon name="chevron-right" size={16} /></button>
    </div>}
    <div className="cards-study-mark card-review-mark">
      <span>這張卡你熟悉嗎？</span>
      <CardMarkButtons current={null} disabled={busy || !loaded} onMark={(rating) => void mark(rating)} label={`標記「${item.card.label}」熟不熟`} />
      <p className="card-review-hint" role="status">{busy ? "正在保存…" : "標成熟悉就會離開需要加強；不熟的卡片下一輪還會出現。"}</p>
      {markError && <p className="form-error" role="alert">標記尚未保存。{markError}<button className="text-button" type="button" onClick={() => void mark(failed.current!)}>重試</button></p>}
    </div>
  </article>;
}


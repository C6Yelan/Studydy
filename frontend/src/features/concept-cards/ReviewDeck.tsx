import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardReviewItem, CardReviewRating, CardReviewsView } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { CardMarkButtons, markLabel, useMarkSubmit } from "./CardMarks";
import "./review-deck.css";
import { PageSkeleton } from "../../ui/Skeleton";

// 加強模式：逐張翻閱標成不熟的卡片並重新標記；一輪看完就停，不自動再拿同一批卡。
export function ReviewDeck({ apiClient, cardSetId, onChange, variant = "page" }: {
  apiClient: StudydyApiClient;
  cardSetId?: string;
  onChange?: (summary: CardReviewsView) => void;
  variant?: "home" | "page";
}) {
  const [cards, setCards] = useState<CardReviewItem[] | null>(null);
  const [flipped, setFlipped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [marked, setMarked] = useState(0);
  const [reload, setReload] = useState(0);
  const failed = useRef<CardReviewRating | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const flipButton = useRef<HTMLButtonElement>(null);
  const submit = useMarkSubmit(apiClient);
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    void apiClient.getCardReviews(cardSetId).then((value) => {
      if (cancelled) return;
      setCards(value.cards);
      setFlipped(false);
      onChangeRef.current?.(value);
    }, (failure) => { if (!cancelled) setLoadError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, cardSetId, reload]);

  const current = cards?.[0];
  const mark = async (rating: CardReviewRating) => {
    if (!current || busy) return;
    setBusy(true);
    setError(null);
    try {
      await submit(current.card_set_id, current.card.concept_id, rating, current.review_count);
      failed.current = null;
      setMessage(rating === "remembered" ? "已標成熟悉，離開需要加強。" : `已標成${markLabel(rating)}，之後還會在需要加強看到它。`);
      setMarked((value) => value + 1);
      setFlipped(false);
      setCards((list) => list?.slice(1) ?? null);
      // 重新讀取張數，讓上方的需要加強與各卡組張數跟上；這一輪的卡片清單不變。
      void apiClient.getCardReviews(cardSetId).then((value) => onChangeRef.current?.(value), () => undefined);
      requestAnimationFrame(() => flipButton.current?.focus({ preventScroll: true }));
    } catch (failure) {
      if (failure instanceof ApiClientError && failure.reasonCode === "CARD_REVIEW_CONFLICT") {
        failed.current = null;
        setMessage(errorMessage(failure));
        setReload((value) => value + 1);
      } else {
        failed.current = rating;
        setError(errorMessage(failure));
      }
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <div className={`review-deck is-${variant}`} role="alert">
    <p>無法讀取需要加強的卡片。{loadError}</p>
    <button className="secondary-button" type="button" onClick={() => setReload((value) => value + 1)}>重新讀取</button>
  </div>;
  if (!cards) return <div className={`review-deck is-${variant}`}><PageSkeleton label="正在讀取需要加強的卡片…" /></div>;
  if (!current) return <div className={`review-deck is-${variant} is-done`}>
    <img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="" />
    <b>{marked ? "這一輪加強完成！" : "目前沒有需要加強的卡片"}</b>
    <small role="status">{marked ? `這一輪標記了 ${marked} 張。` : "瀏覽卡組時把不熟的卡片標起來，就會出現在這裡。"}</small>
    {message && <small>{message}</small>}
    {marked > 0 && <button className="secondary-button" type="button" onClick={() => { setMarked(0); setMessage(null); setCards(null); setReload((value) => value + 1); }}>再加強一輪</button>}
  </div>;
  const claims = current.card.claims.slice(0, 3);
  return <div className={`review-deck is-${variant}`}>
    <div className="review-flip">
      <button ref={flipButton} type="button" className={`review-card${flipped ? " is-flipped" : ""}`} aria-pressed={flipped}
        aria-label={flipped ? `${current.card.label}的教材重點` : `說說看：什麼是「${current.card.label}」？點一下看重點`}
        onClick={() => setFlipped((value) => !value)}>
        <span className="review-face is-front" aria-hidden={flipped}>
          <span className="review-tag">{current.card_set_name} · 目前不熟</span>
          <span className="review-question">說說看：什麼是「{current.card.label}」？</span>
          <small><Icon name="refresh" size={14} />點一下看重點</small>
        </span>
        <span className="review-face is-back" aria-hidden={!flipped}>
          <span className="review-tag">教材重點</span>
          <span className="review-claims">{claims.map((claim) => <span key={claim.claim_id}>{claim.text}</span>)}</span>
          <small>{current.card.claims.length > claims.length ? `還有 ${current.card.claims.length - claims.length} 個重點在卡組中 · ` : ""}現在熟悉了嗎？</small>
        </span>
      </button>
      <span className="review-stack" aria-hidden="true"><span /><span /></span>
    </div>
    <CardMarkButtons current={null} disabled={!flipped || busy} onMark={(rating) => void mark(rating)} />
    <p className="review-status" role="status">{busy ? "正在保存…" : error ? "" : message ?? (flipped ? "還不熟，還是已經熟悉了？" : "先回想，再翻卡核對。")}</p>
    {error && <p className="form-error" role="alert">標記尚未保存。{error}<button className="text-button" type="button" onClick={() => void mark(failed.current!)}>重試</button></p>}
    <button className="text-button review-open-set" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: current.card_set_id, materialId: current.material_id })}>
      在卡組中看完整卡片與來源<Icon name="chevron-right" size={16} />
    </button>
  </div>;
}

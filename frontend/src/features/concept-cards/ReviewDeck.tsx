import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { CardReviewItem, CardReviewQueueView, CardReviewRating } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon, type IconName } from "../../ui/Icon";
import "./review-deck.css";
import { PageSkeleton } from "../../ui/Skeleton";

const grades: { rating: CardReviewRating; label: string; icon: IconName }[] = [
  { rating: "forgot", label: "忘了", icon: "refresh" },
  { rating: "fuzzy", label: "模糊", icon: "eye" },
  { rating: "remembered", label: "記得", icon: "check" },
];

// 間隔來自後端的實際排程結果，不在前端另算。
function nextText(rating: CardReviewRating, days: number) {
  if (rating === "forgot") return "沒關係，10 分鐘後再看一次。";
  return days === 1 ? "明天再複習這張。" : `${days} 天後再複習這張。`;
}

function dueText(at: string) {
  const zone = "Asia/Taipei";
  const day = (value: Date) => new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(value);
  const diff = Math.round((Date.parse(`${day(new Date(at))}T00:00:00Z`) - Date.parse(`${day(new Date())}T00:00:00Z`)) / 86_400_000);
  if (diff <= 0) return `今天 ${new Intl.DateTimeFormat("zh-TW", { timeZone: zone, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(at))}`;
  if (diff === 1) return "明天";
  return `${diff} 天後`;
}

type Pending = { item: CardReviewItem; rating: CardReviewRating; key: string };

export function ReviewDeck({ apiClient, cardSetId, onQueue, variant = "page" }: {
  apiClient: StudydyApiClient;
  cardSetId?: string;
  onQueue?: (queue: CardReviewQueueView) => void;
  variant?: "home" | "page";
}) {
  const [queue, setQueue] = useState<CardReviewQueueView | null>(null);
  const [cards, setCards] = useState<CardReviewItem[]>([]);
  const [flipped, setFlipped] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState(0);
  const [reload, setReload] = useState(0);
  const pending = useRef<Pending | null>(null);
  const onQueueRef = useRef(onQueue);
  onQueueRef.current = onQueue;
  const flipButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    void apiClient.getCardReviewQueue(cardSetId).then((value) => {
      if (cancelled) return;
      setQueue(value);
      setCards(value.cards);
      setFlipped(false);
      onQueueRef.current?.(value);
    }, (failure) => { if (!cancelled) setLoadError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, cardSetId, reload]);

  const current = cards[0];
  const rate = async (rating: CardReviewRating) => {
    if (!current || busy) return;
    // 同一張卡的重試沿用同一個鍵；換評分則是新的請求。
    if (!pending.current || pending.current.item !== current || pending.current.rating !== rating)
      pending.current = { item: current, rating, key: crypto.randomUUID() };
    const request = pending.current;
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.submitCardReview(current.card_set_id, current.card.concept_id, rating, current.review_count, request.key);
      pending.current = null;
      setMessage(nextText(result.rating, result.interval_days));
      setReviewed((value) => value + 1);
      setFlipped(false);
      const rest = cards.slice(1);
      setCards(rest);
      // 這批看完後重新讀取，取得下一批或剛好到期的卡，並更新各卡組張數。
      if (!rest.length) setReload((value) => value + 1);
      else if (queue) {
        const next = {
          ...queue,
          due_count: Math.max(0, queue.due_count - (current.state === "due" ? 1 : 0)),
          new_count: Math.max(0, queue.new_count - (current.state === "new" ? 1 : 0)),
          sets: queue.sets.map((set) => set.card_set_id !== current.card_set_id ? set : {
            ...set,
            due_count: Math.max(0, set.due_count - (current.state === "due" ? 1 : 0)),
            new_count: Math.max(0, set.new_count - (current.state === "new" ? 1 : 0)),
            reviewed_count: set.reviewed_count + (current.state === "new" ? 1 : 0),
          }),
          cards: rest,
        };
        setQueue(next);
        onQueue?.(next);
      }
      requestAnimationFrame(() => flipButton.current?.focus({ preventScroll: true }));
    } catch (failure) {
      if (failure instanceof ApiClientError && failure.reasonCode === "CARD_REVIEW_CONFLICT") {
        pending.current = null;
        setMessage(errorMessage(failure));
        setReload((value) => value + 1);
      } else setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };

  if (loadError) return <div className={`review-deck is-${variant}`} role="alert">
    <p>無法讀取複習清單。{loadError}</p>
    <button className="secondary-button" type="button" onClick={() => setReload((value) => value + 1)}>重新讀取</button>
  </div>;
  if (!queue) return <div className={`review-deck is-${variant}`}><PageSkeleton label="正在讀取今天要複習的卡片…" /></div>;
  if (!current) return <div className={`review-deck is-${variant} is-done`}>
    <img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="" />
    <b>{reviewed ? "今天的複習完成！" : "目前沒有要複習的卡片"}</b>
    <small role="status">{reviewed ? `這一輪複習了 ${reviewed} 張。` : ""}{queue.next_due_at ? `下一張在${dueText(queue.next_due_at)}到期。` : "建立卡組後就能開始複習。"}</small>
    {message && <small>{message}</small>}
  </div>;
  const claims = current.card.claims.slice(0, 3);
  return <div className={`review-deck is-${variant}`}>
    <div className="review-flip">
      <button ref={flipButton} type="button" className={`review-card${flipped ? " is-flipped" : ""}`} aria-pressed={flipped}
        aria-label={flipped ? `${current.card.label}的教材重點` : `說說看：什麼是「${current.card.label}」？點一下看重點`}
        onClick={() => setFlipped((value) => !value)}>
        <span className="review-face is-front" aria-hidden={flipped}>
          <span className="review-tag">{current.card_set_name} · {current.state === "new" ? "新卡" : "到期"}</span>
          <span className="review-question">說說看：什麼是「{current.card.label}」？</span>
          <small><Icon name="refresh" size={14} />點一下看重點</small>
        </span>
        <span className="review-face is-back" aria-hidden={!flipped}>
          <span className="review-tag">教材重點</span>
          <span className="review-claims">{claims.map((claim) => <span key={claim.claim_id}>{claim.text}</span>)}</span>
          <small>{current.card.claims.length > claims.length ? `還有 ${current.card.claims.length - claims.length} 個重點在卡組中 · ` : ""}你記得多少？</small>
        </span>
      </button>
      <span className="review-stack" aria-hidden="true"><span /><span /></span>
    </div>
    <div className="review-grades" role="group" aria-label="評分這張卡">
      {grades.map((grade) => <button key={grade.rating} type="button" className={`is-${grade.rating}`} disabled={!flipped || busy} onClick={() => void rate(grade.rating)}>
        <Icon name={grade.icon} size={20} />{grade.label}
      </button>)}
    </div>
    <p className="review-status" role="status">{busy ? "正在保存…" : error ? "" : message ?? (flipped ? "選一個最接近的感覺。" : "先回想，再翻卡核對。")}</p>
    {error && <p className="form-error" role="alert">評分尚未保存。{error}<button className="text-button" type="button" onClick={() => void rate(pending.current!.rating)}>重試</button></p>}
    <button className="text-button review-open-set" type="button" onClick={() => writeRoute({ name: "card-set", cardSetId: current.card_set_id, materialId: current.material_id })}>
      在卡組中看完整卡片與來源<Icon name="chevron-right" size={16} />
    </button>
  </div>;
}

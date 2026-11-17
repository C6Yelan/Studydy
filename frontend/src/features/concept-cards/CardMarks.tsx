import { useRef, useState } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { CardReviewRating, CardReviewView } from "../../api/contracts";
import { playCue } from "../../ui/feedback-sound";
import "./card-review.css";

// 學習標記：學生自己判斷熟不熟，不排程；標成不熟的卡會列入「需要加強」。
// label 是狀態名稱（提示、清單標記、通知用）；action 是按鈕上的說法。
export const cardMarks: { rating: CardReviewRating; label: string; action: string }[] = [
  { rating: "forgot", label: "不熟", action: "還不熟" },
  { rating: "remembered", label: "熟悉", action: "熟悉了" },
];

export const markLabel = (rating: CardReviewRating) => cardMarks.find((mark) => mark.rating === rating)!.label;

/** 送出標記；同一張卡、同一個標記與次數的重試沿用同一個鍵，網路失敗重送不會重複計次。 */
export function useMarkSubmit(apiClient: StudydyApiClient) {
  const pending = useRef<{ id: string; key: string } | null>(null);
  return (cardSetId: string, conceptId: string, rating: CardReviewRating, expectedCount: number): Promise<CardReviewView> => {
    const id = `${cardSetId}:${conceptId}:${rating}:${expectedCount}`;
    if (pending.current?.id !== id) pending.current = { id, key: crypto.randomUUID() };
    const { key } = pending.current;
    return apiClient.submitCardReview(cardSetId, conceptId, rating, expectedCount, key).then((result) => {
      if (pending.current?.id === id) pending.current = null;
      return result;
    });
  };
}

export function CardMarkButtons({ current, disabled, onMark, label = "標記這張卡", hotkeys = false, title }: {
  current: CardReviewRating | null; disabled?: boolean; onMark: (rating: CardReviewRating) => void; label?: string; hotkeys?: boolean; title?: string;
}) {
  const [popped, setPopped] = useState<CardReviewRating | null>(null);
  return <div className="review-grades" role="group" aria-label={label} title={title} data-silent-click>
    {cardMarks.map((mark) => <button key={mark.rating} type="button" className={`is-${mark.rating}${current === mark.rating ? " is-selected" : ""}${popped === mark.rating ? " mark-pop" : ""}`}
      aria-pressed={current === mark.rating} disabled={disabled} onClick={() => {
        if (mark.rating === "remembered") { playCue("cardKnown"); setPopped(mark.rating); }
        onMark(mark.rating);
      }}>
      {mark.action}{hotkeys && <kbd aria-hidden="true">{mark.rating === "forgot" ? "1" : "2"}</kbd>}
    </button>)}
  </div>;
}

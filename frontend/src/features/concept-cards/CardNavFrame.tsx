import type { ReactNode } from "react";
import { Icon } from "../../ui/Icon";
import "./card-nav-frame.css";

/** 概念卡左右兩側的上一張／下一張：卡片下方不再佔一列，手機寬度改排在卡片下方。 */
export function CardNavFrame({ onPrev, onNext, prevDisabled, nextDisabled, nextLabel = "下一張", children }: {
  onPrev: () => void; onNext: () => void; prevDisabled: boolean; nextDisabled: boolean; nextLabel?: string; children: ReactNode;
}) {
  return <div className="card-nav-frame" role="group" aria-label="概念卡操作">
    <button type="button" className="card-nav-button is-prev" disabled={prevDisabled} onClick={onPrev}><Icon name="chevron-left" size={26} /><span>上一張</span></button>
    <div className="card-nav-body">{children}</div>
    <button type="button" className="card-nav-button is-next" disabled={nextDisabled} onClick={onNext}><Icon name={nextLabel === "下一張" ? "chevron-right" : "check"} size={26} /><span>{nextLabel}</span></button>
  </div>;
}

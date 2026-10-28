import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Icon } from "../../ui/Icon";
import "./card-nav-frame.css";

const BELOW_CARD = 200;

/** 概念卡左右兩側的上一張／下一張：卡片下方不再佔一列，手機寬度改排在卡片下方。 */
export function CardNavFrame({ onPrev, onNext, prevDisabled, nextDisabled, nextLabel = "下一張", children }: {
  onPrev: () => void; onNext: () => void; prevDisabled: boolean; nextDisabled: boolean; nextLabel?: string; children: ReactNode;
}) {
  const frame = useRef<HTMLDivElement>(null);
  // 卡面高度上限 = 視窗高度 − 卡片區上緣 − 卡片下方固定區（完整重點、標記列、提示）；手機改用 CSS 預設值。
  useLayoutEffect(() => {
    const element = frame.current;
    if (!element) return;
    const fit = () => {
      if (window.matchMedia("(max-width: 720px)").matches) { element.style.removeProperty("--card-face-max"); return; }
      const top = element.getBoundingClientRect().top + window.scrollY;
      element.style.setProperty("--card-face-max", `${Math.max(Math.round(window.innerHeight * 0.33), Math.round(window.innerHeight - top - BELOW_CARD))}px`);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  });
  return <div ref={frame} className="card-nav-frame" role="group" aria-label="概念卡操作">
    <button type="button" className="card-nav-button is-prev" disabled={prevDisabled} onClick={onPrev}><Icon name="chevron-left" size={26} /><span>上一張</span></button>
    <div className="card-nav-body">{children}</div>
    <button type="button" className="card-nav-button is-next" disabled={nextDisabled} onClick={onNext}><Icon name={nextLabel === "下一張" ? "chevron-right" : "check"} size={26} /><span>{nextLabel}</span></button>
  </div>;
}

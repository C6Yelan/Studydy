import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Icon } from "../../ui/Icon";
import { Progress } from "../../ui/Progress";
import "./card-nav-frame.css";

/** 卡片外框以外（上方進度條、內距、來源列、下緣操作列）佔用的垂直空間；卡面高度 = 可用高度 − 這個值。 */
const SHELL_CHROME = 134;
const NAV_BUTTON = 54;
const GAP = 28;
const SHELL_PADDING = 70;
const MAX_FACE_WIDTH = 970;

/**
 * 概念卡外框：圓形上一張／下一張在卡片左右，卡片頂端細進度條，下緣浮動操作列（dock）。
 * 量測可用空間後以 --card-face-w／--card-face-h 交給卡面，卡面（canvas）據此等比縮放；手機改由 CSS 排版。
 */
export function CardNavFrame({ onPrev, onNext, prevDisabled, nextDisabled, nextLabel = "下一張", progress, dock, children }: {
  onPrev: () => void; onNext: () => void; prevDisabled: boolean; nextDisabled: boolean; nextLabel?: string;
  progress: { value: number; max: number; label: string }; dock: ReactNode; children: ReactNode;
}) {
  const frame = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = frame.current;
    if (!element) return;
    const fit = () => {
      if (window.matchMedia("(max-width: 720px)").matches) {
        element.style.removeProperty("--card-face-h"); element.style.removeProperty("--card-face-w");
        return;
      }
      const top = element.getBoundingClientRect().top + window.scrollY;
      const height = Math.round(window.innerHeight - top - 28 - SHELL_CHROME);
      const width = Math.min(MAX_FACE_WIDTH, Math.round(element.clientWidth - 2 * (NAV_BUTTON + GAP) - SHELL_PADDING));
      element.style.setProperty("--card-face-h", `${Math.max(height, 120)}px`);
      element.style.setProperty("--card-face-w", `${Math.max(width, 320)}px`);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  });
  return <div ref={frame} className="card-nav-frame" role="group" aria-label="概念卡操作">
    <button type="button" className="card-nav-button is-prev" aria-label="上一張" disabled={prevDisabled} onClick={onPrev}><Icon name="chevron-left" size={26} /></button>
    <div className="card-shell">
      <Progress className="card-shell-progress" max={progress.max} value={progress.value} aria-label={progress.label} />
      <div className="card-shell-face">{children}</div>
      <div className="card-dock" role="group" aria-label="標記與頁數">{dock}</div>
    </div>
    <button type="button" className="card-nav-button is-next" aria-label={nextLabel} disabled={nextDisabled} onClick={onNext}><Icon name={nextLabel === "下一張" ? "chevron-right" : "check"} size={26} /></button>
  </div>;
}

import { useEffect, useState } from "react";
import "./skeleton.css";

/** 骨架畫面的版型：依頁面主要內容的位置排列，讀取完成後換成實際內容。 */
export type SkeletonVariant = "block" | "page" | "library" | "material" | "map" | "deck" | "media";

/** 單一佔位色塊；只供視覺參考，輔助技術讀取外層的狀態文字。 */
export function Bone({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return <span className={`skeleton-bone ${className}`} style={style} aria-hidden="true" />;
}

function Lines({ count, last = "60%" }: { count: number; last?: string }) {
  return <>{Array.from({ length: count }, (_, index) =>
    <Bone key={index} className="is-line" style={index === count - 1 ? { width: last } : undefined} />)}</>;
}

/** 教材頁首：單列的返回＋島嶼縮圖＋名稱＋分頁＋Studydy AI BOT＋頭像（分頁在窄螢幕裁切，與實際頁首橫向捲動一致）。 */
function MaterialBar() {
  return <div className="skeleton-material-bar">
    <Bone className="is-round" /><Bone className="is-thumb" />
    <Bone className="is-line is-name" />
    <div className="skeleton-tabs">{Array.from({ length: 6 }, (_, index) => <Bone key={index} className="is-pill" />)}</div>
    <Bone className="is-pill is-chat" /><Bone className="is-round" />
  </div>;
}

function Cards({ count }: { count: number }) {
  return <div className="skeleton-grid">{Array.from({ length: count }, (_, index) =>
    <div key={index} className="skeleton-card"><Bone className="is-art" /><Lines count={2} /></div>)}</div>;
}

function Body({ variant }: { variant: SkeletonVariant }) {
  if (variant === "block") return <div className="skeleton-stack"><Lines count={3} /></div>;
  if (variant === "library") return <>
    <div className="skeleton-heading"><Bone className="is-line is-title" style={{ width: "min(240px, 60%)" }} /><Bone className="is-line" style={{ width: "min(360px, 80%)" }} /></div>
    <Cards count={6} />
  </>;
  if (variant === "material") return <><MaterialBar /><Cards count={3} /></>;
  if (variant === "map") return <><MaterialBar />
    <div className="skeleton-map">
      <div className="skeleton-map-canvas">
        <Bone className="is-node is-center" /><Bone className="is-node is-left" /><Bone className="is-node is-right" />
      </div>
      <div className="skeleton-map-side"><Bone className="is-line is-title" style={{ width: "50%" }} />{Array.from({ length: 7 }, (_, index) => <Bone key={index} className="is-row" />)}</div>
    </div>
  </>;
  if (variant === "deck") return <><MaterialBar /><div className="skeleton-deck"><Bone className="is-flashcard" /><div className="skeleton-actions"><Bone className="is-pill" /><Bone className="is-pill" /></div></div></>;
  if (variant === "media") return <><MaterialBar /><Bone className="is-video" /><div className="skeleton-stack"><Lines count={3} /></div></>;
  return <>
    <div className="skeleton-heading"><Bone className="is-line is-title" style={{ width: "min(280px, 60%)" }} /><Bone className="is-line" style={{ width: "min(420px, 85%)" }} /></div>
    <div className="skeleton-stack">{Array.from({ length: 3 }, (_, index) => <Bone key={index} className="is-panel" />)}</div>
  </>;
}

/**
 * 讀取中的骨架畫面。狀態文字延遲 350ms 才出現，快速完成的讀取不閃動文字；
 * 光澤動畫在偏好減少動態時停用（skeleton.css）。
 */
export function PageSkeleton({ variant = "block", label, live = false }: { variant?: SkeletonVariant; label: string; live?: boolean }) {
  const [labelVisible, setLabelVisible] = useState(false);
  useEffect(() => {
    setLabelVisible(false);
    const timer = setTimeout(() => setLabelVisible(true), 350);
    return () => clearTimeout(timer);
  }, [label]);
  return <section className={`quiet-loading page-skeleton is-${variant}`} aria-busy="true" aria-live={labelVisible && live ? "polite" : undefined}>
    <Body variant={variant} />
    {labelVisible && <p role="status" className="skeleton-label">{label}</p>}
  </section>;
}

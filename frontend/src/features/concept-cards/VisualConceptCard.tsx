import { useEffect, useMemo, useRef, useState } from "react";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
type ArtworkModule = typeof import("./ConceptCardArtwork");
let cachedArtwork: ArtworkModule | null = null;

export function useConceptArtwork() {
  const [artworkModule, setArtwork] = useState<ArtworkModule | null>(() => cachedArtwork);
  const [loadFailed, setLoadFailed] = useState(false);
  useEffect(() => {
    let active = true;
    void import("./ConceptCardArtwork").then(
      module => { cachedArtwork = module; if (active) setArtwork(module); },
      () => { if (active) setLoadFailed(true); },
    );
    return () => { active = false; };
  }, []);
  return { artworkModule, loadFailed };
}

export function VisualConceptCard({ card, structure, pageIndex }: {
  card: ConceptCard; structure: KnowledgeStructureView; pageIndex?: number;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const pages = useMemo(() => artworkModule?.conceptCardPages(card, structure) ?? [], [artworkModule, card, structure]);
  if (!artworkModule) return <div className="visual-card"><p role="status">{loadFailed ? "圖卡載入失敗，重新整理後再試。仍可查看下方完整重點。" : "正在載入圖卡…"}</p><div className="visual-card-actions"><button className="secondary-button" disabled>下載 PNG</button><button className="secondary-button" disabled>放大檢視</button></div></div>;
  // 預覽直接列出此觀念的所有圖；複習時由卡組的單一導覽控制目前頁。
  return <>{(pageIndex === undefined ? pages.map((_, i) => i) : [pageIndex]).map(index =>
    <CardImage key={index} card={card} artworkModule={artworkModule} pages={pages} pageIndex={index} />
  )}</>;
}

function CardImage({ card, artworkModule, pages, pageIndex }: {
  card: ConceptCard;
  artworkModule: ArtworkModule; pages: ReturnType<ArtworkModule["conceptCardPages"]>; pageIndex: number;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const [enlarged, setEnlarged] = useState(false);
  useEffect(() => { if (enlarged) dialog.current?.showModal(); }, [enlarged]);
  const artwork = useMemo(() => ({ card, layout: pages[pageIndex], pageIndex, pageCount: pages.length }), [card, pages, pageIndex]);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ artwork: typeof artwork; url?: string; failed?: boolean } | null>(null);
  const pngUrl = result?.artwork === artwork ? result.url : undefined;
  const failed = result?.artwork === artwork && result.failed;
  useEffect(() => {
    let active = true;
    let pngUrl: string | undefined;
    setResult(null);
    void (async () => {
      try {
        const png = await artworkModule.conceptCardPng(card, artwork.layout, pageIndex, pages.length);
        if (!active) return;
        pngUrl = URL.createObjectURL(png);
        setResult({ artwork, url: pngUrl });
      } catch {
        if (active) setResult({ artwork, failed: true });
      }
    })();
    return () => {
      active = false;
      if (pngUrl) URL.revokeObjectURL(pngUrl);
    };
  }, [artworkModule, artwork, attempt, card, pageIndex, pages.length]);
  const download = () => {
    if (!pngUrl) return;
    const link = document.createElement("a");
    link.href = pngUrl;
    link.download = `${card.label.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").slice(0, 80) || "概念卡"}${pages.length > 1 ? `-${pageIndex + 1}` : ""}.png`;
    link.click();
  };
  const image = pngUrl && <img className="visual-concept-card" src={pngUrl} width={1200} height={760}
    alt={`${card.label}視覺化概念卡`} title={`${card.label}｜概念卡${pages.length > 1 ? ` · ${pageIndex + 1}/${pages.length}` : ""}`}
    data-layout={artwork.layout.family} data-image-index={pageIndex + 1} data-image-count={pages.length} />;
  return <div className="visual-card">
    {image || <div className="concept-card-placeholder">
      {failed ? <><p role="alert">PNG 圖卡繪製失敗，請再試一次。</p><button type="button" className="secondary-button" onClick={() => setAttempt(value => value + 1)}>重試圖卡</button></>
        : <p role="status">正在產生 PNG 圖卡…</p>}
    </div>}
    <div className="visual-card-actions">
      <button type="button" className="secondary-button" disabled={!pngUrl} onClick={download}>下載 PNG</button>
      <button ref={opener} type="button" className="secondary-button" disabled={!pngUrl} onClick={() => setEnlarged(true)}>放大檢視</button>
    </div>
    {enlarged && <dialog ref={dialog} className="concept-card-zoom" aria-label="放大概念卡" onClose={() => { setEnlarged(false); opener.current?.focus(); }}>
      <header><span>原尺寸圖卡 · 可左右／上下捲動</span><button className="secondary-button" type="button" onClick={() => dialog.current?.close()}>關閉放大</button></header>
      <div className="concept-card-zoom-scroll" tabIndex={0} aria-label="圖卡捲動區">{image}</div>
    </dialog>}
  </div>;
}

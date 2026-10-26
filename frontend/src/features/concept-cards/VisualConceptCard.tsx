import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { ConceptCard, ConceptCardContext } from "../../api/contracts";
type ArtworkModule = typeof import("./ConceptCardArtwork");
let cachedArtwork: ArtworkModule | null = null;

export function useConceptArtwork() {
  const [artworkModule, setArtwork] = useState<ArtworkModule | null>(() => cachedArtwork);
  const [loadFailed, setLoadFailed] = useState(false);
  useEffect(() => {
    let active = true;
    void import("./ConceptCardArtwork").then(
      async module => {
        await Promise.all(["KaTeX_Main", "KaTeX_Math", "KaTeX_Size1", "KaTeX_Size2", "KaTeX_Size3", "KaTeX_Size4"].flatMap(family => [document.fonts.load(`32px ${family}`), document.fonts.load(`italic 32px ${family}`)]));
        cachedArtwork = module; if (active) setArtwork(module);
      },
      () => { if (active) setLoadFailed(true); },
    );
    return () => { active = false; };
  }, []);
  return { artworkModule, loadFailed };
}

// sources：卡面下方與「下載 PNG」同一列、靠左顯示的來源連結。
export function VisualConceptCard({ card, structure, pageIndex, cacheOwner, sources }: {
  card: ConceptCard; structure: ConceptCardContext; pageIndex?: number; cacheOwner: object; sources?: ReactNode;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const pages = useMemo(() => artworkModule?.conceptCardPages(card, structure) ?? [], [artworkModule, card, structure]);
  if (!artworkModule) return <div className="visual-card"><p role="status">{loadFailed ? "圖卡載入失敗，重新整理後再試。仍可查看下方完整重點。" : "正在載入圖卡…"}</p><div className="visual-card-actions">{sources}<DownloadButton disabled /></div></div>;
  // 預覽直接列出此觀念的所有圖；複習時由卡組的單一導覽控制目前頁。
  return <>{(pageIndex === undefined ? pages.map((_, i) => i) : [pageIndex]).map(index =>
    <CardImage key={index} card={card} structure={structure} cacheOwner={cacheOwner} artworkModule={artworkModule} pages={pages} pageIndex={index} sources={sources} />
  )}</>;
}

function DownloadButton({ disabled, downloading = false, onClick }: { disabled: boolean; downloading?: boolean; onClick?: () => void }) {
  return <button type="button" className="secondary-button visual-card-download" disabled={disabled} onClick={onClick}>
    {downloading ? "正在下載…" : "下載 PNG"}
  </button>;
}

function CardImage({ card, structure, cacheOwner, artworkModule, pages, pageIndex, sources }: {
  card: ConceptCard; structure: ConceptCardContext; cacheOwner: object; sources?: ReactNode;
  artworkModule: ArtworkModule; pages: ReturnType<ArtworkModule["conceptCardPages"]>; pageIndex: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const artwork = useMemo(() => ({ card, layout: pages[pageIndex], pageIndex }), [card, pages, pageIndex]);
  const currentArtwork = useRef(artwork);
  currentArtwork.current = artwork;
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ artwork: typeof artwork; failed: boolean } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const ready = result?.artwork === artwork && !result.failed;
  const failed = result?.artwork === artwork && result.failed;
  useLayoutEffect(() => {
    let active = true;
    setDownloadFailed(false);
    setDownloading(false);
    void artworkModule.conceptCardCanvas(card, structure, artwork.layout, pageIndex, pages.length, cacheOwner).then(source => {
      if (!active || !canvas.current) return;
      const context = canvas.current.getContext("2d");
      if (!context) throw new Error("CARD_CANVAS_UNAVAILABLE");
      canvas.current.width = source.width;
      canvas.current.height = source.height;
      context.clearRect(0, 0, source.width, source.height);
      context.drawImage(source, 0, 0);
      setResult({ artwork, failed: false });
    }).catch(() => { if (active) setResult({ artwork, failed: true }); });
    return () => { active = false; };
  }, [artworkModule, artwork, attempt, card, structure, cacheOwner, pageIndex, pages.length]);
  const download = async () => {
    const source = canvas.current;
    if (!ready || !source || downloading) return;
    setDownloading(true); setDownloadFailed(false);
    try {
      const png = await new Promise<Blob>((resolve, reject) => source.toBlob(
        blob => blob ? resolve(blob) : reject(new Error("PNG_ENCODING_FAILED")), "image/png",
      ));
      if (canvas.current !== source || currentArtwork.current !== artwork) return;
      const url = URL.createObjectURL(png);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${card.label.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").slice(0, 80) || "概念卡"}${pages.length > 1 ? `-${pageIndex + 1}` : ""}.png`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      if (canvas.current === source && currentArtwork.current === artwork) setDownloadFailed(true);
    } finally {
      if (canvas.current === source && currentArtwork.current === artwork) setDownloading(false);
    }
  };
  return <div className="visual-card">
    <canvas ref={canvas} className="visual-concept-card" width={1200} height={760} hidden={!ready}
      role="img" aria-label={`${card.label}視覺化概念卡`} title={`${card.label}｜概念卡${pages.length > 1 ? ` · ${pageIndex + 1}/${pages.length}` : ""}`}
      data-layout={artwork.layout.family} data-image-index={pageIndex + 1} data-image-count={pages.length} />
    {!ready && <div className="concept-card-placeholder">
      {failed ? <><p role="alert">圖卡繪製失敗，請再試一次。</p><button type="button" className="secondary-button" onClick={() => setAttempt(value => value + 1)}>重試圖卡</button></>
        : <p role="status">正在繪製圖卡…</p>}
    </div>}
    <div className="visual-card-actions">
      {sources}
      <DownloadButton disabled={!ready || downloading} downloading={downloading} onClick={() => void download()} />
    </div>
    {downloadFailed && <p className="form-error" role="alert">PNG 下載失敗，請再試一次。</p>}
  </div>;
}

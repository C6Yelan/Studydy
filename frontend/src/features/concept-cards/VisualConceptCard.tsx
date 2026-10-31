import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
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

// pageRef：卡面右下角的「教材第 X 頁」；details：點開後從卡片下方滑出的完整重點與來源。
type SourceLayerProps = { pageRef: string; details: ReactNode };
type SourceLayerState = { open: boolean; onOpenChange: (open: boolean) => void };
export function VisualConceptCard({ card, structure, pageIndex, cacheOwner, pageRef, details, downloadName }: {
  card: ConceptCard; structure: ConceptCardContext; pageIndex: number; cacheOwner: object; downloadName: () => Promise<string>;
} & SourceLayerProps) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  // 載入前後的來源面板位於不同元件，開啟狀態留在此處，避免圖卡到達後自動關閉。
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const pages = useMemo(() => artworkModule?.conceptCardPages(card, structure) ?? [], [artworkModule, card, structure]);
  if (!artworkModule) return <><div className="visual-card"><p role="status">{loadFailed ? "圖卡載入失敗，重新整理後再試。仍可從右下角查看完整重點。" : "正在載入圖卡…"}</p></div>
    <SourceLayer open={sourcesOpen} onOpenChange={setSourcesOpen} pageRef={pageRef} details={details} download={<DownloadButton disabled />} /></>;
  // 複習時由卡組的單一導覽控制目前頁。
  return <CardImage key={pageIndex} open={sourcesOpen} onOpenChange={setSourcesOpen} card={card} structure={structure} cacheOwner={cacheOwner} artworkModule={artworkModule} pages={pages} pageIndex={pageIndex} pageRef={pageRef} details={details} downloadName={downloadName} />;
}

function DownloadButton({ disabled, downloading = false, onClick }: { disabled: boolean; downloading?: boolean; onClick?: () => void }) {
  return <button type="button" className="secondary-button visual-card-download" disabled={disabled} onClick={onClick}>
    {downloading ? "正在下載…" : "下載 PNG"}
  </button>;
}

// 卡片右下角的頁數小字與抽屜；點擊只展開／關閉來源，不翻面（面板由外層點擊翻面時略過按鈕）。
function SourceLayer({ pageRef, details, download, open, onOpenChange }: SourceLayerProps & SourceLayerState & { download: ReactNode }) {
  return <>
    <button type="button" className="card-page-ref" aria-expanded={open} aria-controls="card-source-drawer" title="查看完整重點與教材來源" onClick={() => onOpenChange(!open)}>{pageRef}</button>
    {open && <section id="card-source-drawer" className="card-source-drawer" aria-label="完整重點與來源"
      onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onOpenChange(false); } }}>
      <header><h3>完整重點與來源</h3>{download}<button type="button" className="text-button" onClick={() => onOpenChange(false)}>關閉</button></header>
      <div className="card-source-drawer-body">{details}</div>
    </section>}
  </>;
}

function CardImage({ card, structure, cacheOwner, artworkModule, pages, pageIndex, pageRef, details, downloadName, open, onOpenChange }: {
  card: ConceptCard; structure: ConceptCardContext; cacheOwner: object; downloadName: () => Promise<string>;
  artworkModule: ArtworkModule; pages: ReturnType<ArtworkModule["conceptCardPages"]>; pageIndex: number;
} & SourceLayerProps & SourceLayerState) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const artwork = useMemo(() => ({ card, layout: pages[pageIndex], pageIndex }), [card, pages, pageIndex]);
  const currentArtwork = useRef(artwork);
  currentArtwork.current = artwork;
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ artwork: typeof artwork; failed: boolean; height: number } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadFailed, setDownloadFailed] = useState(false);
  const reader = useRef<HTMLDivElement>(null);
  const [moreBelow, setMoreBelow] = useState(false);
  const [scrollable, setScrollable] = useState(false);
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
      setResult({ artwork, failed: false, height: source.height });
    }).catch(() => { if (active) setResult({ artwork, failed: true, height: 0 }); });
    return () => { active = false; };
  }, [artworkModule, artwork, attempt, card, structure, cacheOwner, pageIndex, pages.length]);
  // 卡面縮到下限仍放不進畫面時在卡內捲動：下緣還有內容時淡出並提示，捲到底就消失。
  useLayoutEffect(() => {
    const element = reader.current;
    if (!element || !ready) { setScrollable(false); setMoreBelow(false); return; }
    const update = () => {
      setScrollable(element.scrollHeight - element.clientHeight > 1);
      setMoreBelow(element.scrollHeight - element.scrollTop - element.clientHeight > 4);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [ready, result]);
  const download = async () => {
    const source = canvas.current;
    if (!ready || !source || downloading) return;
    setDownloading(true); setDownloadFailed(false);
    try {
      const png = await new Promise<Blob>((resolve, reject) => source.toBlob(
        blob => blob ? resolve(blob) : reject(new Error("PNG_ENCODING_FAILED")), "image/png",
      ));
      if (canvas.current !== source || currentArtwork.current !== artwork) return;
      const filename = await downloadName();
      if (canvas.current !== source || currentArtwork.current !== artwork) return;
      const url = URL.createObjectURL(png);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      if (canvas.current === source && currentArtwork.current === artwork) setDownloadFailed(true);
    } finally {
      if (canvas.current === source && currentArtwork.current === artwork) setDownloading(false);
    }
  };
  return <><div className="visual-card-frame"><div ref={reader} className={`visual-card${scrollable ? " is-scrollable" : ""}${moreBelow ? " has-more" : ""}`} tabIndex={scrollable ? 0 : undefined}
    aria-label={scrollable ? "卡片內容（可捲動）" : undefined} style={ready ? { "--card-canvas-h": result.height } as CSSProperties : undefined}
    onScroll={event => { const element = event.currentTarget; setMoreBelow(element.scrollHeight - element.scrollTop - element.clientHeight > 4); }}>
    <canvas ref={canvas} className="visual-concept-card" width={1200} height={760} hidden={!ready}
      role="img" aria-label={`${card.label}視覺化概念卡`} title={`${card.label}｜概念卡${pages.length > 1 ? ` · ${pageIndex + 1}/${pages.length}` : ""}`}
      data-layout={artwork.layout.family} data-image-index={pageIndex + 1} data-image-count={pages.length} />
    {!ready && <div className="concept-card-placeholder">
      {failed ? <><p role="alert">圖卡繪製失敗，請再試一次。</p><button type="button" className="secondary-button" onClick={() => setAttempt(value => value + 1)}>重試圖卡</button></>
        : <p role="status">正在繪製圖卡…</p>}
    </div>}
  </div>{moreBelow && <span className="visual-card-more" aria-hidden="true">往下捲看更多 ↓</span>}</div>
  <SourceLayer open={open} onOpenChange={onOpenChange} pageRef={pageRef} details={details} download={<>{downloadFailed && <span className="form-error" role="alert">PNG 下載失敗，請再試一次。</span>}<DownloadButton disabled={!ready || downloading} downloading={downloading} onClick={() => void download()} /></>} /></>;
}

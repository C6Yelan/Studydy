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

export function VisualConceptCard({ card, structure, materialName, pageIndex }: {
  card: ConceptCard; structure: KnowledgeStructureView; materialName: string; pageIndex?: number;
}) {
  const { artworkModule, loadFailed } = useConceptArtwork();
  const pages = useMemo(() => artworkModule?.conceptCardPages(card, structure) ?? [], [artworkModule, card, structure]);
  if (!artworkModule) return <div className="visual-card"><p role="status">{loadFailed ? "圖卡載入失敗，重新整理後再試。仍可查看下方完整重點。" : "正在載入圖卡…"}</p><div className="visual-card-actions"><button className="secondary-button" disabled>下載 SVG</button><button className="secondary-button" disabled>放大檢視</button></div></div>;
  // 預覽直接列出此觀念的所有圖；複習時由卡組的單一導覽控制目前頁。
  return <>{(pageIndex === undefined ? pages.map((_, i) => i) : [pageIndex]).map(index =>
    <CardImage key={index} card={card} structure={structure} materialName={materialName} artworkModule={artworkModule} pages={pages} pageIndex={index} />
  )}</>;
}

function CardImage({ card, structure, materialName, artworkModule, pages, pageIndex }: {
  card: ConceptCard; structure: KnowledgeStructureView; materialName: string;
  artworkModule: ArtworkModule; pages: ReturnType<ArtworkModule["conceptCardPages"]>; pageIndex: number;
}) {
  const Artwork = artworkModule.ConceptCardArtwork;
  const svg = useRef<SVGSVGElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const [enlarged, setEnlarged] = useState(false);
  useEffect(() => { if (enlarged) dialog.current?.showModal(); }, [enlarged]);
  const download = () => {
    if (!svg.current) return;
    const data = new XMLSerializer().serializeToString(svg.current);
    const url = URL.createObjectURL(new Blob([data], { type: "image/svg+xml;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${card.label.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").slice(0, 80) || "概念卡"}${pages.length > 1 ? `-${pageIndex + 1}` : ""}.svg`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const artwork = { card, structure, materialName, layout: pages[pageIndex], pageIndex, pageCount: pages.length };
  return <div className="visual-card">
    <Artwork {...artwork} svgRef={svg} />
    <div className="visual-card-actions">
      <button type="button" className="secondary-button" disabled={!Artwork} onClick={download}>下載 SVG</button>
      <button ref={opener} type="button" className="secondary-button" disabled={!Artwork} onClick={() => setEnlarged(true)}>放大檢視</button>
    </div>
    {enlarged && <dialog ref={dialog} className="concept-card-zoom" aria-label="放大概念卡" onClose={() => { setEnlarged(false); opener.current?.focus(); }}>
      <header><span>原尺寸圖卡 · 可左右／上下捲動</span><button className="secondary-button" type="button" onClick={() => dialog.current?.close()}>關閉放大</button></header>
      <div className="concept-card-zoom-scroll" tabIndex={0} aria-label="圖卡捲動區">{Artwork && <Artwork {...artwork} />}</div>
    </dialog>}
  </div>;
}

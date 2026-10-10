import { useEffect, useRef, useState } from "react";
import type { ConceptCard, KnowledgeStructureView } from "../../api/contracts";
type ArtworkComponent = typeof import("./ConceptCardArtwork")["ConceptCardArtwork"];
let cachedArtwork: ArtworkComponent | null = null;

export function VisualConceptCard({ card, structure, materialName }: {
  card: ConceptCard; structure: KnowledgeStructureView; materialName: string;
}) {
  const [Artwork, setArtwork] = useState<ArtworkComponent | null>(() => cachedArtwork);
  const [loadFailed, setLoadFailed] = useState(false);
  useEffect(() => {
    let active = true;
    void import("./ConceptCardArtwork").then(
      module => { cachedArtwork = module.ConceptCardArtwork; if (active) setArtwork(() => module.ConceptCardArtwork); },
      () => { if (active) setLoadFailed(true); },
    );
    return () => { active = false; };
  }, []);
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
    link.download = `${card.label.replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").slice(0, 80) || "概念卡"}.svg`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const artwork = { card, structure, materialName };
  return <div className="visual-card">
    {Artwork ? <Artwork {...artwork} svgRef={svg} /> : <p role="status">{loadFailed ? "圖卡載入失敗，重新整理後再試。仍可查看下方完整重點。" : "正在載入圖卡…"}</p>}
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

import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { getFontEmbedCSS, toPng } from "html-to-image";
import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeOutlineView } from "../../api/contracts";
import { IslandArt, regionPalette } from "../../ui/art/Island";
import { mapRegions, regionColorIndex } from "../knowledge-map/knowledge-map";
import { relationStyles } from "../knowledge-map/map-appearance";
import "../knowledge-map/styles.css";
import "./map-images.css";

export async function downloadMapImages(api: StudydyApiClient, view: KnowledgeOutlineView) {
  const raw = await api.getKnowledgeRegions({ materialId: view.material_id, structureRevision: view.knowledge_structure_revision });
  const regions = mapRegions(view, raw);
  if (!regions) throw new Error("地圖區域不完整，請重新讀取後匯出。");
  const concepts = new Map(view.concepts.map(c => [c.concept_id, c]));
  const order = new Map(view.initial_learning_path.map(s => [s.concept_id, s.position]));
  const groups = view.concepts.length <= 24 ? [{ label: "完整知識地圖", conceptIds: view.concepts.map(c => c.concept_id) }] : regions.regions;
  const parts = groups.flatMap(group => Array.from({ length: Math.ceil(group.conceptIds.length / 24) }, (_, i) => ({
    label: group.label, ids: group.conceptIds.slice(i * 24, (i + 1) * 24),
  })));
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-100000px;top:0;width:1400px;pointer-events:none;";
  host.setAttribute("aria-hidden", "true");
  document.body.append(host);
  const root = createRoot(host);
  const images: { concept_ids: string[]; png: string }[] = [];
  let fonts: string | undefined;
  try {
    await document.fonts.ready;
    for (const [partIndex, part] of parts.entries()) {
      const relations = view.relations.filter(r => part.ids.includes(r.source_concept_id));
      const within = relations.filter(r => part.ids.includes(r.target_concept_id));
      const sheet = <div className="map-export-sheet">
        <h1>{part.label} · {partIndex + 1}/{parts.length}</h1>
        <p>知識地圖 · 各箭頭保留來源概念 → 目標概念方向</p>
        <div className="map-export-graph">
          <div className="map-export-grid">{part.ids.map(id => {
            const c = concepts.get(id)!;
            return <div key={id} data-concept-id={id} className="concept-flow-node react-flow__node map-export-node">
              <span className="island-sign"><em>{order.get(id)}</em><span><strong>{c.label}</strong><small>{c.claim_count} 個重點</small></span></span>
              <span className="island-node-art"><IslandArt palette={regionPalette(view.material_id, regionColorIndex(regions, regions.byConcept.get(id)!))} /></span>
            </div>;
          })}</div>
        </div>
        <section className="map-export-relations"><h2>概念關係（含跨圖連線）</h2>
          {relations.length ? <ul>{relations.map(r => <li key={r.relation_id}>
            {concepts.get(r.source_concept_id)!.label} → {concepts.get(r.target_concept_id)!.label}：{relationStyles[r.type].label}
            {!part.ids.includes(r.target_concept_id) && "（見其他區域圖）"}
          </li>)}</ul> : <p>此區域沒有向外的概念關係。</p>}
        </section>
      </div>;
      flushSync(() => root.render(sheet));
      const element = host.firstElementChild as HTMLElement;
      const graph = element.querySelector<HTMLElement>(".map-export-graph")!;
      const bounds = graph.getBoundingClientRect();
      const nodes = new Map(Array.from(graph.querySelectorAll<HTMLElement>("[data-concept-id]"), node => [node.dataset.conceptId!, node.getBoundingClientRect()]));
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("width", String(bounds.width)); svg.setAttribute("height", String(bounds.height));
      svg.classList.add("map-export-edges");
      within.forEach((r, i) => {
        const a = nodes.get(r.source_concept_id)!, b = nodes.get(r.target_concept_id)!;
        const right = b.x > a.x;
        const x = (right ? a.right : a.left) - bounds.x, y = a.top + a.height / 2 - bounds.y;
        const tx = (right ? b.left : b.right) - bounds.x, ty = b.top + b.height / 2 - bounds.y;
        const bend = (right ? 1 : -1) * (70 + (i % 3) * 16);
        const g = document.createElementNS(svg.namespaceURI, "g");
        g.setAttribute("class", `concept-flow-edge is-${r.type}`);
        const path = document.createElementNS(svg.namespaceURI, "path");
        path.setAttribute("class", "react-flow__edge-path");
        path.setAttribute("d", `M${x} ${y} C${x + bend} ${y},${tx - bend} ${ty},${tx} ${ty}`);
        g.append(path); svg.append(g);
        const arrow = document.createElementNS(svg.namespaceURI, "path");
        arrow.setAttribute("d", `M${tx - (right ? 12 : -12)} ${ty - 6} L${tx} ${ty} L${tx - (right ? 12 : -12)} ${ty + 6}`);
        arrow.setAttribute("fill", "none"); arrow.setAttribute("stroke", relationStyles[r.type].color); arrow.setAttribute("stroke-width", "3"); svg.append(arrow);
      });
      graph.prepend(svg);
      // 先將橋線依現行 CSS 畫成像素，避免 foreignObject 轉圖漏掉 SVG 群組的水平路徑。
      const bridges = document.createElement("canvas");
      bridges.width = Math.ceil(bounds.width); bridges.height = Math.ceil(bounds.height);
      bridges.className = "map-export-edges";
      const context = bridges.getContext("2d")!;
      for (const path of svg.querySelectorAll("path")) {
        const style = getComputedStyle(path);
        context.strokeStyle = style.stroke;
        context.lineWidth = parseFloat(style.strokeWidth);
        context.lineCap = style.strokeLinecap as CanvasLineCap;
        context.setLineDash(style.strokeDasharray === "none" ? [] : style.strokeDasharray.split(/[,\s]+/).map(parseFloat));
        context.stroke(new Path2D(path.getAttribute("d")!));
      }
      svg.replaceWith(bridges);
      fonts ??= await getFontEmbedCSS(element);
      const image = await toPng(element, { pixelRatio: 1, fontEmbedCSS: fonts });
      images.push({ concept_ids: part.ids, png: image.split(",")[1] });
      bridges.remove();
    }
    await api.downloadExport(`/v1/materials/${view.material_id}/knowledge-structures/${view.knowledge_structure_revision}/map/export/png`, { images });
  } finally {
    root.unmount(); host.remove();
  }
}

import type { CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { getFontEmbedCSS, toPng } from "html-to-image";
import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeOutlineView } from "../../api/contracts";
import { mapRegions } from "../knowledge-map/knowledge-map";
import { relationStyles } from "../knowledge-map/map-appearance";
import "./map-images.css";
import { layoutMap, MAP_NODE_WIDTH } from "./map-image-layout";

export async function downloadMapImages(api: StudydyApiClient, view: KnowledgeOutlineView) {
  const raw = await api.getKnowledgeRegions({ materialId: view.material_id, structureRevision: view.knowledge_structure_revision });
  const regions = mapRegions(view, raw);
  if (!regions) throw new Error("地圖區域不完整，請重新讀取後匯出。");
  const concepts = new Map(view.concepts.map(c => [c.concept_id, c]));
  const order = new Map(view.initial_learning_path.map(s => [s.concept_id, s.position]));
  const groups = regions.regions;
  const parts = groups.flatMap(group => Array.from({ length: Math.ceil(group.conceptIds.length / 24) }, (_, i) => ({
    label: group.label, ids: group.conceptIds.slice(i * 24, (i + 1) * 24),
  })));
  const partByConcept = new Map(parts.flatMap((part, index) => part.ids.map(id => [id, `${part.label}（圖 ${index + 1}）`] as const)));
  const linePatterns = { prerequisite: [], part_of: [12, 4], application: [8, 6], example: [2, 6], contrast: [10, 4, 2, 4] };
  const host = document.createElement("div");
  host.style.cssText = "position:fixed;left:-100000px;top:0;width:1400px;pointer-events:none;";
  host.setAttribute("aria-hidden", "true");
  document.body.append(host);
  const root = createRoot(host);
  const images: { concept_ids: string[]; png: string }[] = [];
  let fonts: string | undefined;
  try {
    await document.fonts.ready;
    // 概念框寬度固定、高度依文字換行；先量測再排版，文字才不會被截斷或重疊。
    flushSync(() => root.render(<div className="map-export-measure" style={{ width: MAP_NODE_WIDTH }}>{view.concepts.map(c =>
      <div key={c.concept_id} data-concept-id={c.concept_id} className="map-export-concept">
        <small>概念 {order.get(c.concept_id)}</small><strong>{c.label}</strong><span>{c.claim_count} 個重點</span></div>)}</div>));
    const heights = new Map(Array.from(host.querySelectorAll<HTMLElement>("[data-concept-id]"), node => [node.dataset.conceptId!, Math.ceil(node.getBoundingClientRect().height)]));
    for (const [partIndex, part] of parts.entries()) {
      const within = view.relations.filter(r => part.ids.includes(r.source_concept_id) && part.ids.includes(r.target_concept_id));
      const crossing = view.relations.filter(r => part.ids.includes(r.source_concept_id) !== part.ids.includes(r.target_concept_id));
      const layout = layoutMap({
        nodes: part.ids.map(id => ({ id, height: heights.get(id)!, order: order.get(id)! })),
        edges: within.map(r => ({ id: r.relation_id, source: r.source_concept_id, target: r.target_concept_id, prerequisite: r.type === "prerequisite" })),
      });
      const sheetWidth = Math.max(960, layout.width + 80);
      const card = (id: string, style?: CSSProperties) => <div key={id} data-concept-id={id} className="map-export-concept" style={style}>
        <small>概念 {order.get(id)}</small><strong>{concepts.get(id)!.label}</strong><span>{concepts.get(id)!.claim_count} 個重點</span>
      </div>;
      const sheet = <div className="map-export-sheet" style={{ width: sheetWidth }}>
        <h1>{part.label} · 圖 {partIndex + 1}/{parts.length}</h1>
        <p>共 {part.ids.length} 個概念；由左至右依先備關係排列，箭頭由來源概念指向目標概念。</p>
        <div className="map-export-key" aria-label="關係圖例">{Object.entries(relationStyles).map(([type, style]) => <span key={type}>
          <svg width="42" height="12" aria-hidden="true"><path d="M0 6H40m-6 -4 6 4 -6 4" fill="none" stroke={style.color} strokeWidth="2" strokeDasharray={linePatterns[type as keyof typeof linePatterns].join(" ")} /></svg>{style.label}
        </span>)}</div>
        <div className="map-export-graph" style={{ width: layout.width, height: layout.height }}>
          {part.ids.filter(id => layout.nodes.has(id)).map(id => { const n = layout.nodes.get(id)!; return card(id, { left: n.x, top: n.y, height: n.height }); })}
        </div>
        {layout.isolated.length > 0 && <section className="map-export-isolated"><h2>未與其他概念連線</h2>
          <div>{layout.isolated.map(id => card(id))}</div>
        </section>}
        {crossing.length > 0 && <section className="map-export-relations"><h2>跨圖關係</h2>
          <ul>{crossing.map(relation => <li key={relation.relation_id}>
            {concepts.get(relation.source_concept_id)!.label} → {concepts.get(relation.target_concept_id)!.label}：{relationStyles[relation.type].label}
            （見 {partByConcept.get(part.ids.includes(relation.source_concept_id) ? relation.target_concept_id : relation.source_concept_id)}）
          </li>)}</ul>
        </section>}
      </div>;
      flushSync(() => root.render(sheet));
      const element = host.firstElementChild as HTMLElement;
      const graph = element.querySelector<HTMLElement>(".map-export-graph")!;
      const bridges = document.createElement("canvas");
      bridges.width = layout.width; bridges.height = layout.height;
      bridges.className = "map-export-edges";
      const context = bridges.getContext("2d")!;
      const types = new Map(within.map(r => [r.relation_id, r.type]));
      for (const [id, edge] of layout.edges) {
        const type = types.get(id)!;
        context.strokeStyle = relationStyles[type].color;
        context.lineWidth = 2.5; context.lineCap = "round"; context.lineJoin = "round";
        context.setLineDash(linePatterns[type]);
        context.beginPath();
        for (const op of edge.ops) {
          if (op[0] === "M") context.moveTo(op[1], op[2]);
          else if (op[0] === "L") context.lineTo(op[1], op[2]);
          else context.bezierCurveTo(op[1], op[2], op[3], op[4], op[5], op[6]);
        }
        context.stroke();
        context.setLineDash([]);
        const { x, y, direction } = edge.arrow;
        context.beginPath(); context.moveTo(x - direction * 9, y - 5); context.lineTo(x, y); context.lineTo(x - direction * 9, y + 5); context.stroke();
      }
      graph.prepend(bridges);
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

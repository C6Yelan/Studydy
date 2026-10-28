import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { getFontEmbedCSS, toPng } from "html-to-image";
import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeOutlineView } from "../../api/contracts";
import { mapRegions } from "../knowledge-map/knowledge-map";
import { relationStyles } from "../knowledge-map/map-appearance";
import "./map-images.css";
import { arrangeMapColumns, mapEdgeLanes } from "./map-image-layout";

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
    for (const [partIndex, part] of parts.entries()) {
      const within = view.relations.filter(r => part.ids.includes(r.source_concept_id) && part.ids.includes(r.target_concept_id));
      const crossing = view.relations.filter(r => part.ids.includes(r.source_concept_id) !== part.ids.includes(r.target_concept_id));
      const prerequisites = within.filter(r => r.type === "prerequisite");
      const ranks = new Map(part.ids.map(id => [id, 0]));
      const pending = new Map(part.ids.map(id => [id, prerequisites.filter(r => r.target_concept_id === id).length]));
      const queue = part.ids.filter(id => pending.get(id) === 0);
      // 先備關係已由結構契約保證無環；其他關係只畫線，不改變學習層次。
      for (const id of queue) for (const relation of prerequisites.filter(r => r.source_concept_id === id)) {
        const target = relation.target_concept_id;
        ranks.set(target, Math.max(ranks.get(target)!, ranks.get(id)! + 1));
        pending.set(target, pending.get(target)! - 1);
        if (pending.get(target) === 0) queue.push(target);
      }
      const columns = arrangeMapColumns(Array.from({ length: Math.max(...ranks.values()) + 1 }, (_, rank) =>
        part.ids.filter(id => ranks.get(id) === rank).sort((a, b) => order.get(a)! - order.get(b)!)), within);
      const rows = new Map(columns.flatMap(ids => ids.map((id, row) => [id, row] as const)));
      const outerLanes = mapEdgeLanes(within.filter(edge => {
        const from = ranks.get(edge.source_concept_id)!, to = ranks.get(edge.target_concept_id)!;
        return from !== to && (to < from || to - from > 1);
      }).map(edge => ({ id: edge.relation_id, start: Math.min(ranks.get(edge.source_concept_id)!, ranks.get(edge.target_concept_id)!), end: Math.max(ranks.get(edge.source_concept_id)!, ranks.get(edge.target_concept_id)!) })));
      const sideLanes = new Map(columns.flatMap(ids => [...mapEdgeLanes(within.filter(edge => ids.includes(edge.source_concept_id) && ids.includes(edge.target_concept_id)).map(edge => ({
        id: edge.relation_id, start: Math.min(rows.get(edge.source_concept_id)!, rows.get(edge.target_concept_id)!), end: Math.max(rows.get(edge.source_concept_id)!, rows.get(edge.target_concept_id)!),
      })))]));
      const sideSpace = Math.max(0, ...sideLanes.values()) * 12;
      const gap = 100 + sideSpace;
      const topSpace = Math.max(64, 24 + (Math.max(-1, ...outerLanes.values()) + 1) * 12);
      const sheet = <div className="map-export-sheet" style={{ width: Math.max(1000, columns.length * (240 + gap) + 80 + sideSpace) }}>
        <h1>{part.label} · 圖 {partIndex + 1}/{parts.length}</h1>
        <p>由左至右依先備關係排列；箭頭由來源概念指向目標概念。</p>
        <div className="map-export-key" aria-label="關係圖例">{Object.entries(relationStyles).map(([type, style]) => <span key={type}>
          <svg width="42" height="12" aria-hidden="true"><path d="M0 6H40m-6 -4 6 4 -6 4" fill="none" stroke={style.color} strokeWidth="2" strokeDasharray={linePatterns[type as keyof typeof linePatterns].join(" ")} /></svg>{style.label}
        </span>)}</div>
        <div className="map-export-graph">
          <div className="map-export-layers" style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))`, columnGap: gap, paddingTop: topSpace, paddingRight: 48 + sideSpace }}>{columns.map((ids, rank) =>
            <div className="map-export-layer" key={rank}>{ids.map(id => {
              const concept = concepts.get(id)!;
              return <div key={id} data-concept-id={id} className="map-export-concept">
                <small>概念 {order.get(id)}</small><strong>{concept.label}</strong><span>{concept.claim_count} 個重點</span>
              </div>;
            })}</div>)}</div>
        </div>
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
      const bounds = graph.getBoundingClientRect();
      const nodes = new Map(Array.from(graph.querySelectorAll<HTMLElement>("[data-concept-id]"), node => [node.dataset.conceptId!, node.getBoundingClientRect()]));
      const bridges = document.createElement("canvas");
      bridges.width = Math.ceil(bounds.width); bridges.height = Math.ceil(bounds.height);
      bridges.className = "map-export-edges";
      const context = bridges.getContext("2d")!;
      within.forEach(relation => {
        const a = nodes.get(relation.source_concept_id)!, b = nodes.get(relation.target_concept_id)!;
        const forward = b.x > a.x;
        const sameColumn = Math.abs(b.x - a.x) < 1;
        const x = (forward || sameColumn ? a.right : a.left) - bounds.x, y = a.top + a.height / 2 - bounds.y;
        const tx = (forward ? b.left : b.right) - bounds.x, ty = b.top + b.height / 2 - bounds.y;
        const bend = 30 + (sideLanes.get(relation.relation_id) ?? 0) * 12;
        context.strokeStyle = relationStyles[relation.type].color;
        context.lineWidth = 2.5; context.lineCap = "round";
        context.setLineDash(linePatterns[relation.type]);
        context.beginPath(); context.moveTo(x, y);
        if (!sameColumn && (!forward || Math.abs(ranks.get(relation.target_concept_id)! - ranks.get(relation.source_concept_id)!) > 1)) {
          const direction = forward ? 1 : -1, lane = 16 + outerLanes.get(relation.relation_id)! * 12;
          context.bezierCurveTo(x + direction * bend, y, x + direction * bend, lane, x + direction * bend, lane);
          context.lineTo(tx - direction * bend, lane);
          context.bezierCurveTo(tx - direction * bend, lane, tx - direction * bend, ty, tx, ty);
        } else if (sameColumn) context.bezierCurveTo(x + bend, y, tx + bend, ty, tx, ty);
        else {
          const direction = forward ? 1 : -1;
          const parallel = within.filter(edge => edge.source_concept_id === relation.source_concept_id && edge.target_concept_id === relation.target_concept_id);
          const offset = (parallel.indexOf(relation) - (parallel.length - 1) / 2) * 18;
          context.bezierCurveTo(x + direction * bend, y + offset, tx - direction * bend, ty + offset, tx, ty);
        }
        context.stroke();
        context.setLineDash([]);
        const arrow = forward ? -1 : 1;
        context.beginPath(); context.moveTo(tx + arrow * 9, ty - 5); context.lineTo(tx, ty); context.lineTo(tx + arrow * 9, ty + 5); context.stroke();
      });
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

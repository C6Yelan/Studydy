import { useRef } from "react";
import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeOutlineView } from "../../api/contracts";
import { ExportControls } from "./ExportControls";

export function MapExports({ api, view }: { api: StudydyApiClient; view: KnowledgeOutlineView }) {
  const menu = useRef<HTMLDetailsElement>(null);
  return <details ref={menu} className="map-export-menu" onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); menu.current!.open = false; menu.current!.querySelector("summary")?.focus(); }
  }}><summary className="secondary-button">匯出地圖</summary><ExportControls label="匯出知識地圖" options={[
    { value: "png", label: "完整地圖 PNG（大教材分區 ZIP）" }, { value: "md", label: "概念大綱 Markdown" },
  ]} download={async format => {
    if (format === "md") return api.downloadExport(`/v1/materials/${view.material_id}/knowledge-structures/${view.knowledge_structure_revision}/map/export/md`);
    const { downloadMapImages } = await import("./map-images");
    await downloadMapImages(api, view);
  }} /></details>;
}

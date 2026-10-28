import type { StudydyApiClient } from "../../api/client";
import type { KnowledgeOutlineView } from "../../api/contracts";
import { ExportControls } from "./ExportControls";

export function MapExports({ api, view }: { api: StudydyApiClient; view: KnowledgeOutlineView }) {
  return <ExportControls label="匯出知識地圖" triggerLabel="匯出地圖" options={[{ value: "md", label: "概念大綱 Markdown" }]}
    download={() => api.downloadExport(`/v1/materials/${view.material_id}/knowledge-structures/${view.knowledge_structure_revision}/map/export/md`)} />;
}

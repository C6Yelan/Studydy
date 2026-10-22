import { useEffect, useState } from "react";
import { errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { writeRoute } from "../../app/routes";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import { StateView } from "../../ui/StateView";
import { rememberMaterial } from "./material-memory";

/** /materials/{id}：讀取教材後導向目前的知識地圖；尚未建立地圖時導向處理進度或來源頁。 */
export function MaterialEntry({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId: string }) {
  const [failure, setFailure] = useState<{ notFound: boolean; message: string } | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setFailure(null);
    void apiClient.getMaterial(materialId).then((material) => {
      if (cancelled) return;
      rememberMaterial(apiClient, material);
      const structure = material.available_structures.find((item) => item.knowledge_structure_revision === material.head_revision)
        ?? material.available_structures[0];
      const run = material.latest_attempt;
      if (structure) writeRoute({ name: "knowledge-map", materialId, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision }, true);
      else if (run && (run.status === "pending" || run.status === "running")) writeRoute({ name: "material-run", materialId, runId: run.run_id }, true);
      else writeRoute({ name: "material-sources", materialId }, true);
    }, (error) => { if (!cancelled) setFailure({ notFound: isNotFound(error), message: errorMessage(error) }); });
    return () => { cancelled = true; };
  }, [apiClient, materialId, reload]);
  if (failure?.notFound) return <NotFoundView {...materialNotFound} />;
  return <StateView title={failure ? "無法讀取教材" : "正在開啟教材"} description={failure?.message ?? "正在找到這份教材目前的知識地圖。"}
    tone={failure ? "failure" : "loading"} live={!failure} skeleton="material"
    action={failure ? <button type="button" className="secondary-button" onClick={() => setReload((value) => value + 1)}>重新讀取</button> : undefined} />;
}

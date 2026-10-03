import { useEffect, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem } from "../../api/contracts";
import { rememberMaterial, rememberedMaterial } from "./material-memory";
import { StateView } from "../../ui/StateView";
import { CardSetLibrary } from "../concept-cards/CardSetLibrary";
import { MaterialContentNav } from "./MaterialContentNav";
import { PodcastLibrary } from "../podcasts/PodcastLibrary";
import { ResearchPanel, type ResearchDraft } from "../material-tools/ResearchPanel";

export function MaterialContent({ apiClient, learnerId, materialId, kind, children, researchDraft, onResearchDraftChange }: {
  apiClient: StudydyApiClient; learnerId: string; materialId: string; kind: "concept-cards" | "podcasts" | "research";
  children?: ReactNode; researchDraft?: ResearchDraft; onResearchDraftChange?: (draft: ResearchDraft) => void;
}) {
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(()=>rememberedMaterial(apiClient,materialId));
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.getMaterial(materialId).then(v => { if (!cancelled) { rememberMaterial(apiClient,v); setMaterial(v); } }, e => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, materialId, reload]);
  const structure = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision) ?? material?.available_structures[0];
  const navigation = <MaterialContentNav apiClient={apiClient} materialId={materialId} current={kind} mapRoute={structure ? { name: "knowledge-map", materialId, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision } : null} />;
  return <section className="material-content">
    {error || !material ? <StateView title={error ? "無法讀取教材" : "正在讀取教材"} description={error ?? "正在載入此教材的學習內容。"} tone={error ? "failure" : "loading"} live={!error}
      action={error ? <button type="button" className="secondary-button" onClick={() => setReload(v => v + 1)}>重新讀取</button> : undefined} /> : <>
      {children != null && navigation}
      <div className="material-content-body">{children != null ? <div role="tabpanel" id={`material-panel-${kind}`} aria-labelledby={`material-tab-${kind}`}>{children}</div> : kind === "research" ? <ResearchPanel api={apiClient} materialId={materialId} materialName={material.display_name} contentNavigation={navigation} draft={researchDraft} onDraftChange={onResearchDraftChange} onMaterialUpdated={()=>setReload(value=>value+1)}/> : kind === "podcasts" ? <PodcastLibrary apiClient={apiClient} learnerId={learnerId} material={material} contentNavigation={navigation}/> : <CardSetLibrary apiClient={apiClient} material={material} contentNavigation={navigation}/>}</div>
    </>}
  </section>;
}

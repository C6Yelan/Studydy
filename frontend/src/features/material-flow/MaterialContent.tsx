import { writeRoute } from "../../app/routes";
import { Notes } from "../notes/Notes";
import { useEffect, useState, type ReactNode } from "react";
import { errorMessage, isNotFound, type StudydyApiClient } from "../../api/client";
import { NotFoundView, materialNotFound } from "../../ui/NotFoundView";
import type { MaterialLibraryItem } from "../../api/contracts";
import { rememberMaterial, rememberedMaterial } from "./material-memory";
import { StateView } from "../../ui/StateView";
import { CardSetLibrary } from "../concept-cards/CardSetLibrary";
import { Breadcrumb } from "../../ui/Breadcrumb";
import { PodcastLibrary } from "../podcasts/PodcastLibrary";
import { ResearchPanel } from "../material-tools/ResearchPanel";
import { ResearchDetail } from "../material-tools/ResearchDetail";
import type { ResearchDraft } from "../material-tools/research";

export function MaterialContent({ apiClient, learnerId, materialId, kind, children, researchId, researchDraft, onResearchDraftChange }: {
  apiClient: StudydyApiClient; learnerId: string; materialId: string; kind: "concept-cards" | "podcasts" | "research" | "notes";
  children?: ReactNode; researchId?: string; researchDraft?: ResearchDraft; onResearchDraftChange?: (draft: ResearchDraft) => void;
}) {
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(()=>rememberedMaterial(apiClient,materialId));
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.getMaterial(materialId).then(v => { if (!cancelled) { rememberMaterial(apiClient,v); setMaterial(v); } }, e => { if (!cancelled) { setNotFound(isNotFound(e)); setError(errorMessage(e)); } });
    return () => { cancelled = true; };
  }, [apiClient, materialId, reload]);
  const structure = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision) ?? material?.available_structures[0];
  // 卡組、Podcast 的子頁自己放麵包屑；找更多資料的子頁（確認範圍、這次找到的資料）在這裡放。
  const back = kind === "research" && (children != null || researchId)
    ? <Breadcrumb current={researchId ? "這次找到的資料" : "確認找資料的範圍"}
      parent={{ label: "找更多資料", ariaLabel: "返回找更多資料", onClick: () => writeRoute({ name: "material-content", materialId, kind }) }} /> : null;
  if (notFound) return <NotFoundView {...materialNotFound} />;
  return <section className={`material-content${kind === "notes" ? " notes-material-content" : ""}`}>
    {error || !material ? <StateView title={error ? "無法讀取教材" : "正在讀取教材"} description={error ?? "正在載入此教材的學習內容。"} tone={error ? "failure" : "loading"} live={!error} skeleton="material"
      action={error ? <button type="button" className="secondary-button" onClick={() => setReload(v => v + 1)}>重新讀取</button> : undefined} /> : <>
      {children != null && back}
      <div className="material-content-body">{children != null ? children : kind === "notes" ? structure ? <Notes api={apiClient} materialId={materialId} revision={structure.knowledge_structure_revision}/> : <p>教材分析完成後即可閱讀筆記。</p> : kind === "research" ? researchId ? <ResearchDetail key={researchId} api={apiClient} materialId={materialId} researchId={researchId} backLink={back} draft={researchDraft} onDraftChange={onResearchDraftChange} onMaterialUpdated={()=>setReload(value=>value+1)}/> : <ResearchPanel api={apiClient} materialId={materialId} draft={researchDraft} onDraftChange={onResearchDraftChange} onMaterialUpdated={()=>setReload(value=>value+1)}/> : kind === "podcasts" ? <PodcastLibrary apiClient={apiClient} learnerId={learnerId} material={material}/> : <CardSetLibrary apiClient={apiClient} material={material}/>}</div>
    </>}
  </section>;
}

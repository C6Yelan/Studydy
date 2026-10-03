import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { CreateCardSet } from "../concept-cards/CreateCardSet";
import { CreatePodcast } from "../podcasts/CreatePodcast";
import { Icon } from "../../ui/Icon";

type CreationRoute = Extract<AppRoute, { name: "podcast-new" | "podcast-create" | "card-set-new" | "card-set-create" }>;

export function CreateStudyContent({ apiClient, route }: { apiClient: StudydyApiClient; route: CreationRoute }) {
  const podcast = route.name.startsWith("podcast");
  const selected = "materialId" in route ? route.materialId : "";
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listMaterials().then((v) => { if (!cancelled) setMaterials(v.materials.filter(m => m.available_structures.some(s => s.knowledge_structure_revision === m.head_revision))); },
      e => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, reload]);
  const materialField = <label className="creation-material">教材<select aria-label="選擇教材" value={selected} disabled={materials === null} onChange={e => {
      const material = materials?.find(m => m.material_id === e.target.value);
      const head = material?.available_structures.find(s => s.knowledge_structure_revision === material.head_revision);
      if (material && head) writeRoute({ name: podcast ? "podcast-create" : "card-set-create", materialId: material.material_id, runId: head.run_id, structureRevision: head.knowledge_structure_revision });
      else writeRoute({ name: podcast ? "podcast-new" : "card-set-new" });
    }}><option value="">{materials === null ? "正在讀取教材…" : "請選擇教材"}</option>{materials?.map(m => <option key={m.material_id} value={m.material_id}>{m.display_name}</option>)}</select></label>;
  return <section className={`cards-page creation-page${podcast ? " podcast-creation-page" : ""}`}>
    {!selected && <button type="button" className="text-button" onClick={() => writeRoute(selected ? { name: "material-content", materialId: selected, kind: podcast ? "podcasts" : "concept-cards" } : { name: podcast ? "podcasts" : "concept-cards" })}><Icon name="arrow-left" size={17} />{podcast ? "我的 Podcast" : "我的概念卡"}</button>}
    <header className="cards-page-header"><div><h1>{podcast ? "建立 Podcast" : "建立概念卡組"}</h1>{route.name !== "podcast-create" && <p>選擇教材，再挑選想複習的概念。</p>}</div></header>
    {route.name !== "podcast-create" && materialField}
    {error && <p role="alert" className="form-error">{error}<button type="button" className="text-button" onClick={() => setReload(v => v + 1)}>重新讀取</button></p>}
    {materials?.length === 0 && <p>尚無可用的教材地圖。<button type="button" className="text-button" onClick={() => writeRoute({ name: "materials" })}>前往我的教材</button></p>}
    {route.name === "podcast-create" ? <CreatePodcast embedded materialField={materialField} key={`${route.materialId}/${route.structureRevision}`} apiClient={apiClient} route={route} />
      : route.name === "card-set-create" ? <CreateCardSet embedded key={`${route.materialId}/${route.structureRevision}`} apiClient={apiClient} route={route} />
      : materials && materials.length > 0 && <div className="creation-placeholder"><Icon name={podcast ? "headphones" : "cards"} size={36} /><p>選好教材後，就能設定名稱、概念與{podcast ? "講解模式" : "推薦方式"}。</p></div>}
  </section>;
}

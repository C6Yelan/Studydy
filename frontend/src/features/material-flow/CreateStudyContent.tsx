import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem } from "../../api/contracts";
import { writeRoute, type AppRoute } from "../../app/routes";
import { CreateCardSet } from "../concept-cards/CreateCardSet";
import { CreatePodcast } from "../podcasts/CreatePodcast";
import { Icon } from "../../ui/Icon";
import { IslandArt, islandPalette } from "../../ui/art/Island";
import { BackLink } from "../../ui/BackLink";
import { EmptyState } from "../../ui/EmptyState";
import { FlowHeader, FlowStage, LearningSteps } from "./Flow";
import "./creation.css";

type CreationRoute = Extract<AppRoute, { name: "podcast-new" | "podcast-create" | "card-set-new" | "card-set-create" }>;

/** 建立卡組／Podcast 的三個步驟：選教材 → 挑概念 → 保存後在卡組／節目頁生成。 */
const cardSetSteps = ["選擇教材", "挑選概念", "開始複習"];
const podcastSteps = ["選擇教材", "挑選概念與形式", "生成 Podcast"];

export function CreateStudyContent({ apiClient, route }: { apiClient: StudydyApiClient; route: CreationRoute }) {
  const podcast = route.name.startsWith("podcast");
  const selected = "materialId" in route ? route.materialId : "";
  const needsMaterialSelection = !selected;
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    if (!needsMaterialSelection) return;
    void apiClient.listMaterials().then((v) => { if (!cancelled) setMaterials(v.materials.filter(m => m.available_structures.some(s => s.knowledge_structure_revision === m.head_revision))); },
      e => { if (!cancelled) setError(errorMessage(e)); });
    return () => { cancelled = true; };
  }, [apiClient, reload, needsMaterialSelection]);
  const choose = (material: MaterialLibraryItem) => {
    const head = material.available_structures.find(s => s.knowledge_structure_revision === material.head_revision);
    if (head) writeRoute({ name: podcast ? "podcast-create" : "card-set-create", materialId: material.material_id, runId: head.run_id, structureRevision: head.knowledge_structure_revision }, false, { fromCollection: true });
  };
  // 選教材用島嶼卡片清單（與我的教材同一套外觀），不用瀏覽器原生下拉選單。
  const materialPicker = <FlowStage icon="book" title="選擇教材" className="creation-picker"
    description={podcast ? "只列出已建立知識地圖的教材，選好後挑選要收聽的概念。" : "只列出已建立知識地圖的教材，選好後挑選要做成卡片的概念。"}
    aside={materials?.length ? <span className="flow-chip is-muted">{materials.length} 份可用</span> : undefined}>
    <div role="group" aria-label="選擇教材">
      {materials === null ? <div className="flow-wait" role="status"><span className="loading-ring" aria-hidden="true" /><p>正在讀取教材…</p></div>
        : <ul className="creation-picker-grid">{materials.map(m => <li key={m.material_id}><button type="button" onClick={() => choose(m)}>
          <span className="creation-picker-thumb" aria-hidden="true"><IslandArt palette={islandPalette(m.material_id)} landmarks={false} /></span>
          <span className="creation-picker-text"><strong>{m.display_name}</strong><small>{(m.source_count ?? 1) > 1 ? `${m.source_count} 個來源` : "1 個來源"} · 建立於 {new Date(m.created_at).toLocaleDateString()}</small></span>
          <Icon name="chevron-right" size={18} />
        </button></li>)}</ul>}
    </div>
  </FlowStage>;
  return <section className={`cards-page creation-page creation-stacked-steps flow-page${podcast ? " podcast-creation-page" : " cards-creation-page"}`}>
    <FlowHeader
      back={needsMaterialSelection && <BackLink label={podcast ? "返回我的 Podcast" : "返回我的概念卡"} route={{ name: podcast ? "podcasts" : "concept-cards" }} />}
      title={podcast ? "建立 Podcast" : "建立概念卡組"}
      description={needsMaterialSelection ? (podcast ? "先選一份教材，再挑選想收聽的概念與講解形式。" : "先選一份教材，再挑選想複習的概念。") : podcast ? "挑選要講解的概念與形式，保存後開始生成。" : "挑選這份教材中想複習的概念。"}
      />
    <LearningSteps labels={podcast ? podcastSteps : cardSetSteps} current={needsMaterialSelection ? 0 : 1} label={podcast ? "建立 Podcast 步驟" : "建立卡組步驟"} />
    {needsMaterialSelection && error && <p role="alert" className="form-error">{error}<button type="button" className="text-button" onClick={() => setReload(v => v + 1)}>重新讀取</button></p>}
    {needsMaterialSelection && materials?.length === 0 ? <EmptyState title="還沒有可用的知識地圖" description={podcast ? "先上傳教材並建立知識地圖，才能把重點做成 Podcast。" : "先上傳教材並建立知識地圖，才能挑選概念做成卡組。"}
      action={<button type="button" className="primary-button" onClick={() => writeRoute({ name: "materials" })}>前往我的教材</button>} />
      : needsMaterialSelection && !error && materialPicker}
    {route.name === "podcast-create" ? <CreatePodcast key={`${route.materialId}/${route.structureRevision}`} apiClient={apiClient} route={route} />
      : route.name === "card-set-create" ? <CreateCardSet embedded key={`${route.materialId}/${route.structureRevision}`} apiClient={apiClient} route={route} />
      : null}
  </section>;
}

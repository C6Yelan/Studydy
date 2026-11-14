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
import { FlowHeader, LearningSteps, podcastSteps } from "./Flow";
import "./creation.css";

type CreationRoute = Extract<AppRoute, { name: "podcast-new" | "podcast-create" | "card-set-new" | "card-set-create" }>;

/** 建立卡組／Podcast 的三個步驟：選教材 → 挑概念 → 保存後在卡組／節目頁生成。 */
const cardSetSteps = ["選一座島", "挑選概念", "開始翻卡"];

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
  // 選島：每份教材一張島嶼卡（與我的群島同一套外觀），不用瀏覽器原生下拉選單。
  const materialPicker = <section className="creation-picker" aria-label="選一座島">
    <div className="creation-say">
      <img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="" />
      <p>{podcast ? "要聽哪一座島的內容？" : "要用哪一座島做卡片？"}<small>只列出已經建好知識地圖的島。</small></p>
      {materials?.length ? <span className="creation-count">{materials.length} 座島可以用</span> : null}
    </div>
    <div role="group" aria-label="選擇教材">
      {materials === null ? <div className="flow-wait" role="status"><span className="loading-ring" aria-hidden="true" /><p>正在讀取教材…</p></div>
        : <ul className="creation-picker-grid">{materials.map(m => <li key={m.material_id}><button type="button" onClick={() => choose(m)}>
          <span className="creation-picker-thumb" aria-hidden="true"><IslandArt palette={islandPalette(m.material_id)} seed={m.material_id} viewBox="-40 25 1120 560" landmarks={false} /></span>
          <span className="creation-picker-text"><span><strong>{m.display_name}</strong><small>{m.source_count ?? 1} 個來源 · 建立於 {new Date(m.created_at).toLocaleDateString("zh-TW", { timeZone: "Asia/Taipei" })}</small></span><Icon name="chevron-right" size={18} /></span>
        </button></li>)}</ul>}
    </div>
  </section>;
  return <section className={`cards-page creation-page creation-stacked-steps flow-page${podcast ? " podcast-creation-page" : " cards-creation-page"}`}>
    <FlowHeader
      back={needsMaterialSelection && <BackLink label={podcast ? "回到 Podcast" : "回到翻卡片"} route={{ name: podcast ? "podcasts" : "concept-cards" }} />}
      title={podcast ? "建立 Podcast" : "建立卡組"}
      description={needsMaterialSelection ? (podcast ? "先選一座島，再挑想收聽的概念與講解形式。" : "先選一座島，再挑想複習的概念。") : podcast ? "挑選要講解的概念與形式，保存後開始生成。" : "挑選這份教材中想複習的概念。"}
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

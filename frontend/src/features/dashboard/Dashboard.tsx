import { useEffect, useState } from "react";
import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import "./styles.css";

export function Dashboard({ apiClient }: { apiClient: StudydyApiClient }) {
  const [materials, setMaterials] = useState<MaterialLibraryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError(null);
    void apiClient.listMaterials().then(library => { if (!cancelled) setMaterials(library.materials); }, failure => { if (!cancelled) setError(errorMessage(failure)); });
    return () => { cancelled = true; };
  }, [apiClient, retry]);
  const usable = (materials ?? []).flatMap(material => {
    const structure = material.available_structures[0];
    return structure ? [{ material, structure }] : [];
  });
  const states = usable.flatMap(({ material, structure }) => {
    const state = material.study_sessions.find(item => item.run_id === structure.run_id && item.knowledge_structure_revision === structure.knowledge_structure_revision);
    return state ? [{ material, state }] : [];
  }).sort((a, b) => Date.parse(b.state.started_at) - Date.parse(a.state.started_at));
  const saved = states.find(item => item.state.status === "active" || item.state.status === "no_safe")
    ?? states.find(item => item.state.status === "completed");
  const map = [...usable].sort((a, b) => Date.parse(b.structure.created_at) - Date.parse(a.structure.created_at))[0];
  const empty = materials?.length === 0;
  return <section className="dashboard">
    <header className="dashboard-header">
      <div><h1>首頁</h1><p>{empty ? "從第一份教材開始你的學習。" : "從最近的學習進度繼續。"}</p></div>
      {!empty && <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>上傳教材</button>}
    </header>
    {error ? <section className="surface dashboard-state" role="alert"><h2>無法讀取學習進度</h2><p>{error}</p><button className="secondary-button" type="button" onClick={() => setRetry(value => value + 1)}>重新讀取</button></section>
      : materials === null ? <section className="surface dashboard-state" role="status"><p>正在讀取學習進度…</p></section>
      : empty ? <section className="dashboard-onboarding surface" aria-label="開始建立你的知識地圖">
        <div><h2>開始建立你的知識地圖</h2><p>上傳 PDF 後，Studydy 會整理教材內容並建立知識地圖。</p><button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>上傳第一份教材</button></div>
        <img src="/assets/Studydy_角色素材/引導/guide_present.png" alt="" />
      </section>
      : saved ? <section className="dashboard-next surface" aria-label="下一步學習">
        <div><p className="eyebrow">{saved.state.status === "completed" ? "最近完成" : "繼續學習"}</p><h2>{saved.material.display_name}</h2></div>
        <button className="primary-button" type="button" onClick={() => writeRoute({ name: "study-session", materialId: saved.material.material_id,
          runId: saved.state.run_id, structureRevision: saved.state.knowledge_structure_revision, studySessionId: saved.state.study_session_id })}>{saved.state.status === "completed" ? "查看學習成果" : "繼續學習"}<Icon name="chevron-right" size={18} /></button>
      </section>
      : map ? <section className="dashboard-next surface" aria-label="下一步學習">
        <div><p className="eyebrow">開始學習</p><h2>{map.material.display_name}</h2></div>
        <button className="primary-button" type="button" onClick={() => writeRoute({ name: "knowledge-map", materialId: map.material.material_id, runId: map.structure.run_id,
          structureRevision: map.structure.knowledge_structure_revision })}>開啟知識地圖<Icon name="chevron-right" size={18} /></button>
      </section>
      : <section className="dashboard-next surface" aria-label="教材處理入口"><p>教材已保存，可前往教材庫查看處理狀態。</p><button className="primary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>前往我的教材</button></section>}
  </section>;
}

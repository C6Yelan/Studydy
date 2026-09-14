import { useEffect, useRef, useState } from "react";

import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem, MaterialStructureLink, StudySessionLink } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialRunStartControl } from "./MaterialRunStartControl";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import { formatFileSize, materialFailureMessage, materialProgressStageLabel, materialRunLabel } from "./material-flow";

function openStructure(item: MaterialLibraryItem, structure: MaterialStructureLink) {
  writeRoute({ name: "knowledge-map", materialId: item.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision });
}

function openStudy(item: MaterialLibraryItem, session: StudySessionLink) {
  writeRoute({ name: "study-session", materialId: item.material_id, runId: session.run_id,
    structureRevision: session.knowledge_structure_revision, studySessionId: session.study_session_id });
}

export function MaterialLibrary({ apiClient, materialId, mapsOnly = false }: { apiClient: StudydyApiClient; materialId?: string; mapsOnly?: boolean }) {
  const [items, setItems] = useState<MaterialLibraryItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const pendingRemovals = useRef(new Set<string>());
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      try {
        const materials = materialId ? [await apiClient.getMaterial(materialId)] : (await apiClient.listMaterials()).materials;
        if (cancelled) return;
        setItems(materials);
        setMessage(null);
        for (const id of pendingRemovals.current) {
          if (!materials.some(item => item.material_id === id)) pendingRemovals.current.delete(id);
        }
        if (pendingRemovals.current.size > 0 || materials.some(item => item.latest_attempt?.status === "pending" || item.latest_attempt?.status === "running")) {
          timer = window.setTimeout(read, 3000);
        }
      } catch (error) {
        if (!cancelled && materialId && pendingRemovals.current.has(materialId) && error instanceof ApiClientError && error.reasonCode === "RESOURCE_NOT_FOUND") {
          writeRoute({ name: "materials" }); return;
        }
        if (!cancelled) setMessage(errorMessage(error));
      }
    };
    void read();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiClient, materialId, reload]);

  const isCollection = !materialId;
  const libraryClass = `material-library${isCollection ? " is-collection" : " is-detail"}${mapsOnly ? " is-maps-only" : ""}`;
  if (message || items === null) {
    const state = message ? <StateView title="無法讀取教材" description={message} tone="failure" action={<>
      <button className="primary-button" type="button" onClick={() => { setMessage(null); setItems(null); setReload(value => value + 1); }}>重新讀取</button>
      <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>返回教材庫</button>
    </>} /> : <StateView title="正在讀取教材庫" description="正在載入你的教材與已發布結果。" tone="loading" live />;
    return isCollection ? <section className={libraryClass}>{state}</section> : state;
  }
  const visibleItems = mapsOnly ? items.filter(item => item.available_structures.length > 0) : items;
  const noPublishedMaps = mapsOnly && items.length > 0 && visibleItems.length === 0;
  return <section className={libraryClass}>
    <header className="library-header">
      <div><h1>{materialId ? "教材詳情" : mapsOnly ? "知識地圖" : "我的教材"}</h1><p className="library-subtitle">{materialId ? "查看已保存的教材、地圖與學習紀錄。" : mapsOnly ? "從已發布的教材地圖開始探索。" : items.length === 0 ? "上傳教材後，可在這裡查看處理結果並接續學習。" : `已保存 ${items.length} 份教材，隨時接續你的學習。`}</p></div>
      {(!isCollection || visibleItems.length > 0) && <div className="state-actions">
        {materialId && <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>返回教材庫</button>}
        {isCollection && <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>上傳教材</button>}
      </div>}
    </header>
    {visibleItems.length === 0 && <section className="library-empty surface" aria-label={mapsOnly ? "知識地圖引導" : "空教材引導"}>
      <div className="library-empty-illustration"><img src="/assets/Studydy_角色素材/空資料/empty_disappointed.png" alt="Studydy 坐在打開的空箱子旁" /></div>
      <h2>{mapsOnly ? noPublishedMaps ? "尚無可開啟的知識地圖" : "尚未建立知識地圖" : "尚未有學習教材"}</h2>
      <p>{mapsOnly ? noPublishedMaps ? "已有教材，但目前還沒有已發布的知識地圖。前往我的教材查看處理狀態。" : "先上傳第一份 PDF，Studydy 會協助你整理知識點並建立知識地圖。" : "先上傳第一份 PDF，讓 Studydy 陪你展開學習。"}</p>
      <button className="primary-button" type="button" onClick={() => writeRoute({ name: noPublishedMaps ? "materials" : "upload" })}>
        <Icon name={noPublishedMaps ? "book" : "upload"} size={18} />{noPublishedMaps ? "前往我的教材" : "上傳第一份教材"}
      </button>
    </section>}
    <div className={materialId ? "library-detail" : "library-grid"}>
    {visibleItems.map(item => {
      const latest = item.latest_attempt;
      const available = item.available_structures;
      const latestHasPublishedMap = !!latest && available.some(structure => structure.run_id === latest.run_id);
      const latestCompletedWithMap = !!latest && (latest.status === "succeeded" || latest.status === "partial") && latestHasPublishedMap;
      const showLatestState = !isCollection || !latestCompletedWithMap;
      const showLatestProcessing = !!latest && (!isCollection || !latestCompletedWithMap);
      const processingPrimary = isCollection && !mapsOnly && !item.study_sessions[0] && !available[0];
      const unpublishedNote = available.length === 0 && <p>目前沒有可開啟的已發布知識地圖。</p>;
      const studyAction = item.study_sessions[0] && <button className={mapsOnly ? "secondary-button" : "primary-button"} type="button" onClick={() => openStudy(item, item.study_sessions[0])}>{item.study_sessions[0].status === "completed" ? "查看上次學習" : "接續上次學習"}</button>;
      const mapAction = available[0] && <button className={mapsOnly || !item.study_sessions[0] ? "primary-button" : "secondary-button"} type="button" onClick={() => openStructure(item, available[0])}>開啟知識地圖</button>;
      const removeControl = !mapsOnly && available.length === 0 && item.study_sessions.length === 0 && (!latest || latest.status === "failed" || latest.status === "cancelled") &&
          <MaterialRemoveControl inActionRow={isCollection} apiClient={apiClient} materialId={item.material_id} onAccepted={state => {
            if (state === "removed") {
              pendingRemovals.current.delete(item.material_id);
              if (materialId) writeRoute({ name: "materials" });
              else setItems(previous => previous?.filter(saved => saved.material_id !== item.material_id) ?? null);
            } else pendingRemovals.current.add(item.material_id);
            setReload(value => value + 1);
          }} />;
      if (!isCollection) return <article className="surface material-detail-card" key={item.material_id} aria-label={item.display_name}>
        <header className="material-detail-identity">
          <span className="library-file-icon" aria-hidden="true"><Icon name="file" size={25} /></span>
          <div><h2>{item.display_name}</h2><p>{new Date(item.created_at).toLocaleString()} · {formatFileSize(item.size_bytes)}</p><a className="text-button material-source-link" href={apiClient.sourceArtifactUrl(item.source_artifact_id)} target="_blank" rel="noopener noreferrer">開啟原始 PDF <span aria-hidden="true">↗</span></a></div>
        </header>
        <section className="material-detail-status" aria-label="最新處理">
          <h3>最新處理</h3>
          <span className={`library-state is-${latest?.status ?? "uploaded"}`}>{latest ? materialRunLabel(latest.status, latest.cancel_requested_at) : "已上傳，尚未開始處理"}</span>
          {latest && (latest.status === "running" || latest.status === "pending") && <p>{materialProgressStageLabel(latest.progress_stage)} · 已完成 {latest.completed_pages} 頁{latest.total_pages !== null && `／共 ${latest.total_pages} 頁`}</p>}
          {latest?.status === "failed" && <p>{materialFailureMessage(latest.error_code ?? "").replace("沒有發布知識地圖", "沒有發布新的知識地圖")}</p>}
          {latest?.status === "failed" && available.length > 0 && <p>先前已發布的知識地圖仍可使用。</p>}
          <div className="material-detail-actions">
            {latest?.status === "pending" || latest?.status === "running" ?
              <button className="primary-button" type="button" onClick={() => writeRoute({ name: "material-run", materialId: item.material_id, runId: latest.run_id })}>查看處理狀態</button> : <>
              {studyAction}{mapAction}
              {(!latest || (latest.status === "failed" && item.study_sessions[0]?.status !== "completed")) && <MaterialRunStartControl key={`${item.material_id}:${latest?.run_id ?? "new"}`} apiClient={apiClient} materialId={item.material_id} sourceArtifactId={item.source_artifact_id} initial={!latest} primary={!item.study_sessions[0] && !available[0]} />}
              {latest?.status === "failed" && <button className={item.study_sessions[0] || available[0] ? "text-button" : "secondary-button"} type="button" onClick={() => writeRoute({ name: "material-run", materialId: item.material_id, runId: latest.run_id })}>查看失敗詳情</button>}
            </>}
          </div>
        </section>
        {item.study_sessions.length > 0 && <section className="material-detail-records" aria-label="學習紀錄">
          <h3>既有學習紀錄</h3><ul>{item.study_sessions.map((session, index) => <li key={session.study_session_id}>
            <div><strong>{session.status === "completed" ? "已完成" : session.status === "no_safe" ? "目前沒有安全題目" : "學習中"}</strong><time dateTime={session.started_at}>{new Date(session.started_at).toLocaleString()}</time></div>
            <button className="secondary-button" type="button" onClick={() => openStudy(item, session)}>開啟學習紀錄 {item.study_sessions.length - index}<Icon name="chevron-right" size={16} /></button>
          </li>)}</ul>
        </section>}
        {available.length > 0 && <section className="material-detail-records" aria-label="已發布版本">
          <h3>已發布版本</h3><ul>{available.map((structure, index) => <li key={structure.knowledge_structure_revision}>
            <div><strong>{structure.status === "partial" ? "部分內容待複核" : "處理完成"}</strong><time dateTime={structure.created_at}>{new Date(structure.created_at).toLocaleString()}</time></div>
            <button className="secondary-button" type="button" onClick={() => openStructure(item, structure)}>開啟版本 {available.length - index}<Icon name="chevron-right" size={16} /></button>
          </li>)}</ul>
        </section>}
        {removeControl && <section className="material-detail-danger" aria-label="移除教材"><h3>移除教材</h3><p>若不再需要這份教材，可將它移除。移除後將無法再從教材庫開啟。</p>{removeControl}</section>}
      </article>;
      return <article className="surface library-item" key={item.material_id} aria-label={item.display_name}>
        <span className="library-file-icon" aria-hidden="true"><Icon name={mapsOnly ? "map" : "file"} size={25} /></span>
        <h2><button className="library-title" type="button" onClick={() => writeRoute({ name: "material-detail", materialId: item.material_id })}>{item.display_name}</button></h2>
        <p>{new Date(item.created_at).toLocaleString()} · {formatFileSize(item.size_bytes)}</p>
        {showLatestState && <p className={`library-state is-${latest?.status ?? "uploaded"}`}>最新處理：{latest ? materialRunLabel(latest.status, latest.cancel_requested_at) : "已上傳，尚未開始處理"}</p>}
        {latest && (latest.status === "running" || latest.status === "pending") && <p>{materialProgressStageLabel(latest.progress_stage)} · 已完成 {latest.completed_pages} 頁{latest.total_pages !== null && `／共 ${latest.total_pages} 頁`}</p>}
        {latest?.status === "failed" && <p>{materialFailureMessage(latest.error_code ?? "")}{available.length > 0 && " 先前已發布的知識地圖仍可開啟。"}</p>}
        {isCollection && unpublishedNote}
        <div className="state-actions">
          {mapsOnly ? mapAction : studyAction}
          {mapsOnly ? studyAction : mapAction}
          {showLatestProcessing && <button className={processingPrimary ? "primary-button" : "secondary-button"} type="button" onClick={() => writeRoute({ name: "material-run", materialId: item.material_id, runId: latest.run_id })}>查看最新處理</button>}
          {isCollection && removeControl}
        </div>
      </article>;
    })}
    </div>
  </section>;
}

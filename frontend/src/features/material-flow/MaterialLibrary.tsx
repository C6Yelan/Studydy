import { useEffect, useRef, useState } from "react";

import { errorMessage, type StudydyApiClient } from "../../api/client";
import type { MaterialLibraryItem, MaterialStructureLink, StudySessionLink } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialRunStartControl } from "./MaterialRunStartControl";
import { MaterialManagement } from "./MaterialManagement";
import { formatFileSize, materialFailureMessage, materialProgressStageLabel, materialRunLabel } from "./material-flow";

function openStructure(item: MaterialLibraryItem, structure: MaterialStructureLink) {
  writeRoute({ name: "knowledge-map", materialId: item.material_id, runId: structure.run_id, structureRevision: structure.knowledge_structure_revision });
}

function openStudy(item: MaterialLibraryItem, session: StudySessionLink) {
  writeRoute({ name: "study-session", materialId: item.material_id, runId: session.run_id,
    structureRevision: session.knowledge_structure_revision, studySessionId: session.study_session_id });
}

export function MaterialLibrary({ apiClient }: { apiClient: StudydyApiClient }) {
  const [items, setItems] = useState<MaterialLibraryItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const searchInput = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const pendingRemovals = useRef(new Set<string>());
  const mutationVersion = useRef(0);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      const version = mutationVersion.current;
      try {
        const materials = (await apiClient.listMaterials()).materials;
        if (cancelled) return;
        if (version !== mutationVersion.current) { timer = window.setTimeout(read, 3000); return; }
        setItems(materials);
        setMessage(null);
        for (const id of pendingRemovals.current) {
          if (!materials.some(item => item.material_id === id)) pendingRemovals.current.delete(id);
        }
        if (pendingRemovals.current.size > 0 || materials.some(item => item.latest_attempt?.status === "pending" || item.latest_attempt?.status === "running")) {
          timer = window.setTimeout(read, 3000);
        }
      } catch (error) {
        if (!cancelled) {
          if (version === mutationVersion.current) setMessage(errorMessage(error));
          else timer = window.setTimeout(read, 3000);
        }
      }
    };
    void read();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiClient, reload]);

  const libraryClass = "material-library is-collection";
  if (message || items === null) {
    const state = message ? <StateView title="無法讀取教材" description={message} tone="failure" action={<>
      <button className="primary-button" type="button" onClick={() => { setMessage(null); setItems(null); setReload(value => value + 1); }}>重新讀取</button>
      <button className="secondary-button" type="button" onClick={() => writeRoute({ name: "materials" })}>返回教材庫</button>
    </>} /> : <StateView title="正在讀取教材庫" description="正在載入你的教材與已發布結果。" tone="loading" live />;
    return <section className={libraryClass}>{state}</section>;
  }
  const normalize = (text: string) => text.trim().replace(/\s+/gu, " ").toLocaleLowerCase();
  const query = normalize(searchQuery);
  const filteredItems = items.filter(item => normalize(item.display_name).includes(query));
  const clearSearch = () => { setSearchQuery(""); searchInput.current?.focus(); };
  const restoreLibraryFocus = () => (searchInput.current ?? heading.current)?.focus({ preventScroll: true });
  return <section className={libraryClass}>
    <header className="library-header">
      <div><h1 ref={heading} tabIndex={-1}>我的教材</h1><p className="library-subtitle">{items.length === 0 ? "上傳教材後，可在這裡查看處理結果並接續學習。" : `已保存 ${items.length} 份教材，隨時接續你的學習。`}</p></div>
      {(items.length > 0) && <div className="state-actions">
        <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>上傳教材</button>
      </div>}
    </header>
    {items.length > 0 && <form className="library-search" role="search" onSubmit={event => event.preventDefault()}>
      <input ref={searchInput} type="search" aria-label="搜尋教材名稱" placeholder="搜尋教材名稱…" value={searchQuery}
        onChange={event => setSearchQuery(event.target.value)} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); clearSearch(); } }} />
    </form>}
    {items.length > 0 && query && filteredItems.length === 0 && <div className="library-search-empty" role="status">
      <h2>找不到符合「{query}」的教材</h2><p>試試其他教材名稱。</p>
      <button className="text-button" type="button" onClick={clearSearch}>清除搜尋</button>
    </div>}
    {items.length === 0 && <section className="library-empty surface" aria-label="空教材引導">
      <div className="library-empty-illustration"><img src="/assets/Studydy_角色素材/空資料/empty_disappointed.png" alt="Studydy 坐在打開的空箱子旁" /></div>
      <h2>尚未有學習教材</h2>
      <p>先上傳第一份 PDF，讓 Studydy 陪你展開學習。</p>
      <button className="primary-button" type="button" onClick={() => writeRoute({ name: "upload" })}>
        <Icon name="upload" size={18} />上傳第一份教材
      </button>
    </section>}
    <div className="library-grid">
    {filteredItems.map(item => {
      const latest = item.latest_attempt;
      const available = item.available_structures;
      const latestHasPublishedMap = !!latest && available.some(structure => structure.run_id === latest.run_id);
      const latestCompletedWithMap = !!latest && (latest.status === "succeeded" || latest.status === "partial") && latestHasPublishedMap;
      const showLatestState = !latestCompletedWithMap;
      const structure = available[0];
      const learningState = structure && item.study_sessions.find(state => state.run_id === structure.run_id && state.knowledge_structure_revision === structure.knowledge_structure_revision);
      const busyRun = latest?.status === "pending" || latest?.status === "running";
      const studyAction = learningState && <button className="primary-button" type="button" onClick={() => openStudy(item, learningState)}>{learningState.status === "completed" ? "查看學習成果" : "繼續學習"}</button>;
      const mapAction = structure && <button className={!learningState ? "primary-button" : "secondary-button"} type="button" onClick={() => openStructure(item, structure)}>開啟知識地圖</button>;
      const deleting = pendingRemovals.current.has(item.material_id);
      return <article className={`surface library-item${deleting ? " is-deleting" : ""}`} key={item.material_id} aria-label={item.display_name}>
        <span className="library-file-icon" aria-hidden="true"><Icon name="file" size={25} /></span>
        <MaterialManagement item={item} apiClient={apiClient} deleting={deleting} onRenamed={updated => {
          mutationVersion.current++;
          setItems(previous => previous?.map(saved => saved.material_id === updated.material_id ? updated : saved) ?? null);
          if (!normalize(updated.display_name).includes(query)) restoreLibraryFocus();
        }} onDeleted={state => {
          mutationVersion.current++;
          if (state === "removed") {
            pendingRemovals.current.delete(item.material_id);
            setItems(previous => previous?.filter(saved => saved.material_id !== item.material_id) ?? null);
            if (items.length === 1) heading.current?.focus({ preventScroll: true }); else restoreLibraryFocus();
          } else pendingRemovals.current.add(item.material_id);
          setReload(value => value + 1);
        }} />
        <p>{new Date(item.created_at).toLocaleString()} · {formatFileSize(item.size_bytes)}</p>
        <a className="text-button material-source-link" href={deleting ? undefined : apiClient.sourceArtifactUrl(item.source_artifact_id)} target="_blank" rel="noopener noreferrer" aria-disabled={deleting || undefined} tabIndex={deleting ? -1 : undefined}>原始 PDF <span aria-hidden="true">↗</span></a>
        {showLatestState && <p className={`library-state is-${latest?.status ?? "uploaded"}`}>最新處理：{latest ? materialRunLabel(latest.status, latest.cancel_requested_at) : "已上傳，尚未開始處理"}</p>}
        {latest && (latest.status === "running" || latest.status === "pending") && <p>{materialProgressStageLabel(latest.progress_stage)} · 已完成 {latest.completed_pages} 頁{latest.total_pages !== null && `／共 ${latest.total_pages} 頁`}</p>}
        {latest?.status === "failed" && <p>{materialFailureMessage(latest.error_code ?? "")}{available.length > 0 && " 先前已發布的知識地圖仍可開啟。"}</p>}
        <fieldset className="state-actions" disabled={deleting}>
          {busyRun ? <button className="primary-button" type="button" onClick={() => writeRoute({ name: "material-run", materialId: item.material_id, runId: latest.run_id })}>查看處理狀態</button> : <>
            {studyAction}{mapAction}
            {(!latest || latest.status === "failed") && learningState?.status !== "completed" && <MaterialRunStartControl key={`${item.material_id}:${latest?.run_id ?? "new"}`} apiClient={apiClient} materialId={item.material_id} sourceArtifactId={item.source_artifact_id} initial={!latest} primary={!structure && !learningState} />}
            {latest?.status === "failed" && <button className={structure ? "text-button" : "secondary-button"} type="button" onClick={() => writeRoute({ name: "material-run", materialId: item.material_id, runId: latest.run_id })}>查看失敗詳情</button>}
          </>}
        </fieldset>
      </article>;
    })}
    </div>
  </section>;
}

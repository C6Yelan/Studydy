import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { SourceListView, MaterialLibraryItem, FormatCapability } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import { formatFileSize, validateSourceFile } from "./material-flow";

type QueuedFile = { file: File; key: string; mediaType: string; error: string | null; status: "pending" | "uploading" | "failed" | "uploaded" };

export function SourceView({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId: string }) {
  const [data, setData] = useState<SourceListView | null>(null);
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const discardAccepted = useRef(false);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [reload, setReload] = useState(0);
  const [selectionError, setSelectionError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const seenSources = useRef(new Set<string>());
  const uploadedKeys = useRef(new Set<string>());
  const intent = useRef({ signature: "", key: crypto.randomUUID() });
  const [formats, setFormats] = useState<FormatCapability[]>([{ extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }]);
  const [formatsReady, setFormatsReady] = useState(false);
  const [capabilityError, setCapabilityError] = useState("");
  useEffect(() => {
    let cancelled = false;
    void apiClient.sourceCapabilities().then(value => { if (!cancelled) { setFormats(value.formats); setFormatsReady(true); } }, () => {
      if (!cancelled) { setCapabilityError("其他格式目前無法載入，仍可上傳 PDF。"); setFormatsReady(true); }
    });
    return () => { cancelled = true; };
  }, [apiClient]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      // 只收斂這次 GET 開始前已確認上傳成功的項目，避免較舊的 polling 回應提前清掉 local row。
      const refreshedKeys = new Set(uploadedKeys.current);
      try {
        const [sources, item] = await Promise.all([apiClient.getSources(materialId), apiClient.getMaterial(materialId)]);
        if (cancelled) return;
        if ([...uploadedKeys.current].some(key => !refreshedKeys.has(key))) {
          timer = window.setTimeout(read, 1500);
          return;
        }
        const staged = sources.sources.filter(source => !source.included);
        const added = staged.filter(source => !seenSources.current.has(source.normalization_id)).map(source => source.normalization_id);
        setSelected(previous => [...previous.filter(id => staged.some(source => source.normalization_id === id)), ...added]);
        seenSources.current = new Set(sources.sources.map(source => source.normalization_id));
        setData(sources); setMaterial(item);
        setQueue(previous => previous.filter(item => !refreshedKeys.has(item.key)));
        for (const key of refreshedKeys) uploadedKeys.current.delete(key);
        if (sources.discard_requested) { discardAccepted.current = true; setRemoving(true); }
        if (discardAccepted.current || sources.sources.some(source => source.status === "pending" || source.status === "running") || item.latest_attempt?.status === "running" || item.latest_attempt?.status === "pending") timer = window.setTimeout(read, 1500);
      } catch (failure) {
        if (cancelled) return;
        if (discardAccepted.current && failure instanceof ApiClientError && failure.reasonCode === "RESOURCE_NOT_FOUND") { writeRoute({ name: "materials" }); return; }
        setError(errorMessage(failure));
      }
    };
    void read();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [apiClient, materialId, reload]);
  const current = material?.available_structures.find(item => item.knowledge_structure_revision === material.head_revision) ?? material?.available_structures[0];
  const baseRevision = current?.knowledge_structure_revision ?? null;
  const run = material?.latest_attempt;
  const active = run?.status === "pending" || run?.status === "running";
  const staged = data?.sources.filter(source => !source.included) ?? [];
  const additions = selected.flatMap(id => staged.filter(source => source.normalization_id === id));
  const displayedSources = current ? data?.sources ?? [] : additions;
  const ready = additions.length > 0 && additions.every(source => source.status === "ready");
  const hasUploads = current ? staged.length > 0 : (data?.sources.length ?? 0) > 0;
  const flowStarted = !!run && run.status !== "failed" && run.status !== "cancelled" && (!current || !!run.base_revision);
  const flowComplete = flowStarted && (run?.status === "succeeded" || run?.status === "partial");
  const openMap = () => { if (current) writeRoute({ name: "knowledge-map", materialId, runId: current.run_id, structureRevision: current.knowledge_structure_revision }); };
  const perform = async (action: () => Promise<void>) => {
    if (submitting.current || discardAccepted.current) return;
    submitting.current = true; setBusy(true); setError(null);
    try { await action(); } catch (failure) { setError(errorMessage(failure)); }
    finally { submitting.current = false; setBusy(false); }
  };
  const start = () => perform(async () => {
    if (!ready || active || queue.length > 0) return;
    const ids = additions.map(source => source.normalization_id);
    const signature = JSON.stringify([baseRevision, ids]);
    if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    const created = await apiClient.createRevision(materialId, ids, intent.current.key, baseRevision);
    writeRoute({ name: "material-run", materialId, runId: created.run_id });
  });
  const chooseFiles = (files: FileList | null) => {
    if (!files?.length || submitting.current || discardAccepted.current) return;
    if (!formatsReady) { setSelectionError("請待支援格式載入後重新選擇教材。"); return; }
    const additions: QueuedFile[] = [];
    const rejected: string[] = [];
    for (const file of Array.from(files)) {
      const problem = validateSourceFile(file, formats);
      if (problem) rejected.push(`「${file.name}」：${problem}`);
      else additions.push({ file, key: crypto.randomUUID(), mediaType: formats.find(format => file.name.toLowerCase().endsWith(format.extension))!.media_type, status: "pending", error: null });
    }
    setSelectionError(rejected.join(" "));
    if (additions.length) setQueue(previous => [...previous, ...additions]);
  };
  const upload = () => perform(async () => {
    for (const item of queue.filter(item => item.status !== "uploaded")) {
      setQueue(previous => previous.map(value => value.key === item.key ? { ...value, status: "uploading", error: null } : value));
      try {
        await apiClient.uploadSource(materialId, item.file, item.mediaType, item.key);
        uploadedKeys.current.add(item.key);
        setQueue(previous => previous.map(value => value.key === item.key ? { ...value, status: "uploaded", error: null } : value));
      } catch (failure) {
        setQueue(previous => previous.map(value => value.key === item.key ? { ...value, status: "failed", error: errorMessage(failure) } : value));
      }
    }
    setReload(value => value + 1);
  });
  if (!data && !error) return <StateView title="正在讀取教材轉換狀態" description="已上傳的原檔會保留，重新整理不會重複轉換。" tone="loading" live />;
  const waiting = additions.filter(source => source.status === "pending" || source.status === "running").length;
  const failed = additions.filter(source => source.status === "failed").length;
  const prepared = additions.filter(source => source.status === "ready").length;
  const pendingUploads = queue.filter(item => item.status === "pending").length;
  const failedUploads = queue.filter(item => item.status === "failed").length;
  const uploading = queue.filter(item => item.status === "uploading").length;
  const uploaded = queue.filter(item => item.status === "uploaded").length;
  const uploadNeeded = pendingUploads + failedUploads + uploading > 0;
  const summary = queue.length === 0 && ready
    ? `${additions.length} 份教材 · 共 ${additions.reduce((sum, source) => sum + (source.page_count ?? 0), 0)} 頁`
    : additions.length === 0 && queue.length === 0 ? "0 份教材"
    : [`${prepared} 份${failed ? "可用" : "已準備"}`,
      waiting > 0 && `${waiting} 份正在轉換`, failed > 0 && `${failed} 份轉換失敗`,
      pendingUploads > 0 && `${pendingUploads} 份待上傳`, uploading > 0 && `${uploading} 份上傳中`,
      failedUploads > 0 && `${failedUploads} 份上傳失敗`, uploaded > 0 && `${uploaded} 份已上傳`,
    ].filter(Boolean).join(" · ");
  return <section className="task-page source-page">
    <header className="upload-hero"><img src="/assets/studydy/upload-guide.png" alt="" /><div><h1>{current ? "新增教材" : "確認教材"}</h1><p>{current ? "追加來源，更新目前地圖並保留未變內容的學習進度。" : "預覽教材內容、調整順序，確認後開始建立知識地圖。"}</p></div></header>
    {error && <div role="alert" className="surface source-request-error"><p className="form-error">{error}</p><button className="secondary-button" onClick={() => { setError(null); setReload(value => value + 1); }}>重新讀取</button></div>}
    <div className="upload-layout"><div>
      <section className="surface source-list-card" aria-label="教材來源">
        <header className="source-list-header"><div><h2 ref={heading} tabIndex={-1}>教材來源</h2><p>{displayedSources.length + queue.length} 份{queue.length === 0 && displayedSources.length > 0 && displayedSources.every(source => source.status === "ready") && ` · ${displayedSources.reduce((sum, source) => sum + (source.page_count ?? 0), 0)} 頁`}</p></div>
          {current && <button className="secondary-button" onClick={openMap}>開啟目前地圖</button>}
        </header>
        {removing && <p role="status">正在刪除教材…</p>}
        <ol className="source-list">
          {displayedSources.map((source, index) => <li className="source-row" key={source.source_id} aria-label={source.original_name}>
            <div className="source-order"><span className="source-number">{index + 1}</span></div>
            <div className="source-content"><strong className="source-name">{source.original_name}</strong>
              <div className="source-metadata">{source.page_count !== null && <span>{source.page_count} 頁</span>}{current && source.included && <span>目前地圖使用中</span>}</div>
              {source.status !== "ready" && <p className={source.status === "failed" ? "form-error" : "source-progress"} role="status">{source.status === "pending" ? "等待轉換" : source.status === "running" ? "正在轉換…" : "轉換失敗"}</p>}
              <div className="source-row-actions">
                {source.normalized_artifact_id && <a className="text-button" href={removing ? undefined : `/v1/artifacts/${encodeURIComponent(source.normalized_artifact_id)}`} aria-disabled={removing || undefined} tabIndex={removing ? -1 : undefined} target="_blank" rel="noopener noreferrer">預覽 PDF</a>}
                <a className="text-button source-download" href={removing ? undefined : `/v1/artifacts/${source.original_artifact_id}/download`} aria-disabled={removing || undefined} tabIndex={removing ? -1 : undefined} target="_blank" rel="noopener noreferrer">下載原檔</a>
                {!source.included && !removing && <>
                  <button className="text-button source-remove" type="button" disabled={busy || active || source.status === "running"} onClick={() => void perform(async () => { await apiClient.removeStagedSource(materialId, source.source_id); setReload(value => value + 1); heading.current?.focus({ preventScroll: true }); })}>移除</button>
                  {current && <label><input type="checkbox" checked={selected.includes(source.normalization_id)} disabled={busy || active} onChange={event => setSelected(previous => event.target.checked ? [...previous, source.normalization_id] : previous.filter(id => id !== source.normalization_id))} /> 加入這次更新</label>}
                  {source.status === "failed" && <button className="secondary-button" disabled={busy} onClick={() => void perform(async () => { await apiClient.retryNormalization(materialId, source.normalization_id); setReload(value => value + 1); })}>重試轉換</button>}
                </>}
              </div>
              {source.error_code && <details className="processing-technical source-technical"><summary>查看錯誤資訊</summary><code>{source.error_code}</code></details>}
            </div>
              {!current && !source.included && !removing && additions.length > 1 && <div className="source-reorder">
                <button type="button" disabled={busy || active || index === 0} aria-label={`上移 ${source.original_name}`} onClick={() => setSelected(previous => { const next = [...previous]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next; })}>↑</button>
                <button type="button" disabled={busy || active || index === additions.length - 1} aria-label={`下移 ${source.original_name}`} onClick={() => setSelected(previous => { const next = [...previous]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next; })}>↓</button>
              </div>}
          </li>)}
          {queue.map((item, index) => <li className="source-row source-upload-row" key={item.key} aria-label={item.file.name}>
            <div className="source-order"><span className="source-number">{displayedSources.length + index + 1}</span></div>
            <div className="source-content"><strong className="source-name">{item.file.name}</strong>
              <div className="source-metadata"><span>{formatFileSize(item.file.size)}</span><span className={`source-upload-status is-${item.status}`} role="status">{{ pending: "待上傳", uploading: "上傳中…", failed: "上傳失敗", uploaded: "已上傳" }[item.status]}</span></div>
              {item.error && <p className="form-error" role="alert">{item.error}</p>}
              {!removing && <div className="source-row-actions"><button className="text-button source-remove" disabled={busy || item.status === "uploaded"} onClick={() => setQueue(previous => previous.filter(value => value.key !== item.key))}>移除</button></div>}
            </div>
          </li>)}
        </ol>
        {!removing && !active && <div className="source-add-area">
          <label className="source-add-control">＋ 新增教材<input type="file" multiple accept={formats.map(format => format.extension).join(",")} aria-label="選擇新增教材" aria-describedby={selectionError ? "source-selection-error" : undefined} disabled={busy || !formatsReady} onChange={event => { chooseFiles(event.currentTarget.files); event.currentTarget.value = ""; }} /></label>
          {capabilityError && <p className="conversion-note" role="status">{capabilityError}</p>}
          {selectionError && <p id="source-selection-error" className="form-error" role="alert">{selectionError}</p>}

        </div>}
        {!removing && <footer className="source-list-footer">
          <div><p className="source-summary">{current ? "本次新增 " : ""}{summary}</p>
            {!active && !uploadNeeded && uploaded > 0 && <p role="status">正在更新教材來源…</p>}
            {!active && queue.length === 0 && waiting > 0 && <p role="status">等待教材轉換完成</p>}
            {failed > 0 && <p className="form-error" role="status">請重試轉換、移除{current ? "或取消勾選" : ""}失敗的教材。</p>}
          </div>
          <div className="source-primary-actions">
            {active && run ? <button className="primary-button" onClick={() => writeRoute({ name: "material-run", materialId, runId: run.run_id })}>查看處理狀態</button>
              : uploadNeeded ? <button className="primary-button" disabled={busy} onClick={() => void upload()}>{uploading ? "正在上傳…" : failedUploads ? "重試上傳" : "上傳新增教材"}</button>
              : queue.length === 0 && ready && <button className="primary-button" disabled={busy} onClick={() => void start()}>{busy ? "正在建立分析…" : current ? "確認新增並更新地圖" : "開始分析教材"}</button>}
            {run && !active && <button className="text-button" onClick={() => writeRoute({ name: "material-run", materialId, runId: run.run_id })}>查看處理結果</button>}
          </div>
        </footer>}
      </section>
      <div className="source-footer"><button className="text-button" onClick={() => writeRoute({ name: "materials" })}><Icon name="arrow-left" />返回教材庫</button>
        <MaterialRemoveControl inActionRow material={material} sources={data?.sources} apiClient={apiClient} materialId={materialId} onAccepted={state => { if (state === "removed") writeRoute({ name: "materials" }); else { discardAccepted.current = true; setRemoving(true); setReload(value => value + 1); } }} />
      </div>
    </div><aside className="surface guide-card source-guide"><h2>{current ? "更新教材" : "從教材到知識地圖"}</h2><ol>
      <li className={hasUploads || flowStarted ? "is-complete" : "is-active"} aria-current={!hasUploads && !flowStarted ? "step" : undefined}><span>1</span><div><strong>上傳教材</strong><p>保留各份原檔與 PDF。</p></div></li>
      <li className={flowStarted ? "is-complete" : hasUploads ? "is-active" : undefined} aria-current={hasUploads && !flowStarted ? "step" : undefined}><span>2</span><div><strong>確認來源</strong><p>預覽後選擇這次加入的內容。</p></div></li>
      <li className={flowComplete ? "is-complete" : flowStarted ? "is-active" : undefined} aria-current={flowStarted && !flowComplete ? "step" : undefined}><span>3</span><div><strong>{current ? "增量更新地圖" : "開始分析教材"}</strong><p>由你確認開始，失敗時保留目前資料。</p></div></li>
    </ol></aside></div>
  </section>;
}

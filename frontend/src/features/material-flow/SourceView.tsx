import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { SourceListView, MaterialLibraryItem, FormatCapability } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import { formatFileSize, validateSourceFile } from "./material-flow";

type QueuedFile = { file: File; key: string; mediaType: string; error: string | null; uploaded: boolean };

export function SourceView({ apiClient, materialId }: { apiClient: StudydyApiClient; materialId: string }) {
  const [data, setData] = useState<SourceListView | null>(null);
  const [material, setMaterial] = useState<MaterialLibraryItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const discardAccepted = useRef(false);
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const [reload, setReload] = useState(0);
  const [queue, setQueue] = useState<QueuedFile[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const seenSources = useRef(new Set<string>());
  const intent = useRef({ signature: "", key: crypto.randomUUID() });
  const [formats, setFormats] = useState<FormatCapability[]>([{ extension: ".pdf", media_type: "application/pdf", max_bytes: 104857600 }]);
  useEffect(() => { let cancelled = false; void apiClient.sourceCapabilities().then(value => { if (!cancelled) setFormats(value.formats); }, () => {}); return () => { cancelled = true; }; }, [apiClient]);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const read = async () => {
      try {
        const [sources, item] = await Promise.all([apiClient.getSources(materialId), apiClient.getMaterial(materialId)]);
        if (cancelled) return;
        const staged = sources.sources.filter(source => !source.included);
        const added = staged.filter(source => !seenSources.current.has(source.normalization_id)).map(source => source.normalization_id);
        setSelected(previous => [...previous.filter(id => staged.some(source => source.normalization_id === id)), ...added]);
        seenSources.current = new Set(sources.sources.map(source => source.normalization_id));
        setData(sources); setMaterial(item);
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
    if (!ready || active || queue.some(item => !item.uploaded)) return;
    const ids = additions.map(source => source.normalization_id);
    const signature = JSON.stringify([baseRevision, ids]);
    if (intent.current.signature !== signature) intent.current = { signature, key: crypto.randomUUID() };
    const created = await apiClient.createRevision(materialId, ids, intent.current.key, baseRevision);
    writeRoute({ name: "material-run", materialId, runId: created.run_id });
  });
  const chooseFiles = (files: FileList | null) => {
    if (!files) return;
    const chosen = Array.from(files);
    setQueue(previous => [...previous, ...chosen.map(file => {
      const format = formats.find(item => file.name.toLowerCase().endsWith(item.extension));
      return { file, key: crypto.randomUUID(), mediaType: format?.media_type ?? "", uploaded: false,
        error: validateSourceFile(file, formats) };
    })]);
  };
  const upload = () => perform(async () => {
    for (const item of queue.filter(item => !item.uploaded)) {
      if (validateSourceFile(item.file, formats)) continue;
      try {
        await apiClient.uploadSource(materialId, item.file, item.mediaType, item.key);
        setQueue(previous => previous.map(value => value.key === item.key ? { ...value, uploaded: true, error: null } : value));
      } catch (failure) {
        setQueue(previous => previous.map(value => value.key === item.key ? { ...value, error: errorMessage(failure) } : value));
      }
    }
    setQueue(previous => previous.filter(item => !item.uploaded));
    setReload(value => value + 1);
  });
  if (!data && !error) return <StateView title="正在讀取教材轉換狀態" description="已上傳的原檔會保留，重新整理不會重複轉換。" tone="loading" live />;
  return <section className="task-page source-page">
    <header className="upload-hero"><img src="/assets/studydy/upload-guide.png" alt="" /><div><h1>{current ? "新增教材" : "教材轉換"}</h1><p>{current ? "追加來源，更新目前地圖並保留未變內容的學習進度。" : "將教材轉換為 PDF，確認內容後即可開始分析。"}</p></div></header>
    {error && <div role="alert" className="surface source-request-error"><p className="form-error">{error}</p><button className="secondary-button" onClick={() => { setError(null); setReload(value => value + 1); }}>重新讀取</button></div>}
    <div className="upload-layout"><div>
      {current && <section className="surface processing-card"><h2>目前教材</h2><p>更新期間可繼續學習；新增內容處理完成後會更新地圖，需複核的內容會保留提示。</p><button className="secondary-button" onClick={openMap}>開啟目前地圖</button></section>}
      {displayedSources.map((source, index) => <section className="surface processing-card source-card" key={source.source_id} aria-label={source.original_name}>
        <h2>{source.included ? "目前使用的來源" : current ? "轉換狀態" : `來源 ${index + 1}`}</h2>
        <div className="chosen-file source-file"><span className="library-file-icon"><Icon name="file" size={24} /></span><div><strong>{source.original_name}</strong><small>原始教材已保留</small></div></div>
        <p role="status">{removing ? "正在刪除教材…" : source.status === "pending" ? "等待轉換" : source.status === "running" ? "正在轉換成 PDF…" : source.status === "failed" ? "轉換失敗，原檔仍保留。" : "轉換完成"}</p>
        <div className="source-file-actions">
          {source.normalized_artifact_id && <a className="secondary-button" href={removing ? undefined : apiClient.sourceArtifactUrl(source.normalized_artifact_id)} target="_blank" rel="noopener noreferrer">預覽轉換後 PDF{source.page_count !== null && `（${source.page_count} 頁）`}</a>}
          <a className="text-button" href={removing ? undefined : `/v2/artifacts/${source.original_artifact_id}`} target="_blank" rel="noopener noreferrer">下載原檔</a>
        </div>
        {!source.included && !removing && <div className="source-next-action">
          {current && <label><input type="checkbox" checked={selected.includes(source.normalization_id)} disabled={busy || active} onChange={event => setSelected(previous => event.target.checked ? [...previous, source.normalization_id] : previous.filter(id => id !== source.normalization_id))} /> 加入這次更新</label>}
          {source.status === "failed" && <button className="secondary-button" disabled={busy} onClick={() => void perform(async () => { await apiClient.retryNormalization(materialId, source.normalization_id); setReload(value => value + 1); })}>重試轉換</button>}
          {!current && additions.length > 1 && <div className="state-actions">
            <button className="text-button" disabled={busy || active || index === 0} aria-label={`上移 ${source.original_name}`} onClick={() => setSelected(previous => { const next = [...previous]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next; })}>上移</button>
            <button className="text-button" disabled={busy || active || index === additions.length - 1} aria-label={`下移 ${source.original_name}`} onClick={() => setSelected(previous => { const next = [...previous]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; return next; })}>下移</button>
          </div>}
          <button className="text-button" disabled={busy || active || source.status === "running"} onClick={() => void perform(async () => { await apiClient.removeStagedSource(materialId, source.source_id); setReload(value => value + 1); })}>{current ? "移除這份新增教材" : "移除這份教材"}</button>
        </div>}
        {source.error_code && <details className="processing-technical source-technical"><summary>查看錯誤資訊</summary><code>{source.error_code}</code></details>}
      </section>)}
      {!removing && !active && <section className="surface processing-card"><h2>選擇新增檔案</h2>
        <label className="file-drop"><Icon name="upload" /><strong>選擇一份或多份教材</strong><span>每份最多 100 MiB，各自保留 PDF</span><input type="file" multiple accept={formats.map(format => format.extension).join(",")} aria-label="選擇新增教材" disabled={busy} onChange={event => { chooseFiles(event.currentTarget.files); event.currentTarget.value = ""; }} /></label>
        {queue.map(item => <div className="chosen-file source-queued-file" key={item.key}><div><strong>{item.file.name}</strong><small>{formatFileSize(item.file.size)} · {item.uploaded ? "已上傳" : "尚未上傳"}</small>{item.error && <p className="form-error" role="alert">{item.error}</p>}</div><button className="text-button" disabled={busy} onClick={() => setQueue(previous => previous.filter(value => value.key !== item.key))}>移除清單項目</button></div>)}
        {queue.some(item => !item.uploaded) && <button className="primary-button" disabled={busy || queue.some(item => validateSourceFile(item.file, formats))} onClick={() => void upload()}>{busy ? "正在上傳…" : "上傳選取的教材"}</button>}
      </section>}
      {!removing && <section className="surface processing-card source-confirmation"><h2>{current ? "確認這次更新" : "確認分析清單"}</h2>
        <p>已選 {additions.length} 份來源{ready && `，共 ${additions.reduce((total, source) => total + (source.page_count ?? 0), 0)} 頁`}{!current && material && `，原檔共 ${formatFileSize(material.size_bytes)}`}。請先預覽 PDF，確認內容、順序與版面。</p>
        {!ready && additions.length > 0 && <p>選取的來源全部轉換完成後才能開始；無效檔案需明確移除或取消勾選。</p>}
        {active && run ? <button className="primary-button" onClick={() => writeRoute({ name: "material-run", materialId, runId: run.run_id })}>查看處理狀態</button> : <button className="primary-button" disabled={busy || !ready || queue.some(item => !item.uploaded)} onClick={() => void start()}>{busy ? "正在建立分析…" : current ? "確認新增並更新地圖" : "開始分析教材"}</button>}
        {run && !active && <button className="text-button" onClick={() => writeRoute({ name: "material-run", materialId, runId: run.run_id })}>查看處理結果</button>}
      </section>}
      <div className="source-footer"><button className="text-button" onClick={() => writeRoute({ name: "materials" })}><Icon name="arrow-left" />返回教材庫</button><MaterialRemoveControl inActionRow hasConversion apiClient={apiClient} materialId={materialId} onAccepted={state => { if (state === "removed") writeRoute({ name: "materials" }); else { discardAccepted.current = true; setRemoving(true); setReload(value => value + 1); } }} /></div>
    </div><aside className="surface guide-card source-guide"><h2>{current ? "更新教材" : "從教材到知識地圖"}</h2><ol>
      <li className={hasUploads || flowStarted ? "is-complete" : "is-active"} aria-current={!hasUploads && !flowStarted ? "step" : undefined}><span>1</span><div><strong>上傳教材</strong><p>保留各份原檔與 PDF。</p></div></li>
      <li className={flowStarted ? "is-complete" : hasUploads ? "is-active" : undefined} aria-current={hasUploads && !flowStarted ? "step" : undefined}><span>2</span><div><strong>確認來源</strong><p>預覽後選擇這次加入的內容。</p></div></li>
      <li className={flowComplete ? "is-complete" : flowStarted ? "is-active" : undefined} aria-current={flowStarted && !flowComplete ? "step" : undefined}><span>3</span><div><strong>{current ? "增量更新地圖" : "開始分析教材"}</strong><p>由你確認開始，失敗時保留目前資料。</p></div></li>
    </ol><div className="upload-requirements"><h3>轉換提醒</h3><p>建議優先使用 PDF。其他格式自動轉為 PDF，轉換品質不保證。</p>{current && <p>未變知識點可承接作答證據；新增或改變內容仍需學習與驗證。</p>}</div></aside></div>
  </section>;
}

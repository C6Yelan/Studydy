import { useEffect, useRef, useState } from "react";
import { ApiClientError, errorMessage, type StudydyApiClient } from "../../api/client";
import type { SourceListView, MaterialLibraryItem } from "../../api/contracts";
import { writeRoute } from "../../app/routes";
import { Icon } from "../../ui/Icon";
import { StateView } from "../../ui/StateView";
import { MaterialRemoveControl } from "./MaterialRemoveControl";
import { formatFileSize } from "./material-flow";

export function SourceView({apiClient,materialId}:{apiClient:StudydyApiClient;materialId:string}) {
  const [data,setData]=useState<SourceListView|null>(null);
  const [material,setMaterial]=useState<MaterialLibraryItem|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [removing,setRemoving]=useState(false);const discardAccepted=useRef(false);
  const [busy,setBusy]=useState(false);const submitting=useRef(false);
  const [reload,setReload]=useState(0);const key=useRef(crypto.randomUUID());
  useEffect(()=>{
    let cancelled=false;let timer:number|undefined;
    const read=async()=>{
      try {
        const [sources,item]=await Promise.all([apiClient.getSources(materialId),apiClient.getMaterial(materialId)]);
        if(cancelled)return;setData(sources);setMaterial(item);setError(null);
        if(sources.discard_requested){discardAccepted.current=true;setRemoving(true);}
        if(discardAccepted.current || sources.sources.some(s=>s.status==="pending"||s.status==="running"))timer=window.setTimeout(read,1500);
      }catch(e){if(!cancelled){if(discardAccepted.current && e instanceof ApiClientError && e.reasonCode==="RESOURCE_NOT_FOUND"){writeRoute({name:"materials"});return;}setError(errorMessage(e));}}
    };void read();return()=>{cancelled=true;window.clearTimeout(timer);};
  },[apiClient,materialId,reload]);
  const source=data?.sources[0];
  const run=material?.latest_attempt;
  const start=async()=>{
    if(submitting.current||!source||discardAccepted.current)return;
    submitting.current=true;setBusy(true);setError(null);
    try {
      const created=await apiClient.createRevision(materialId,source.normalization_id,key.current);
      writeRoute({name:"material-run",materialId,runId:created.run_id});
    }catch(e){setError(errorMessage(e));submitting.current=false;setBusy(false);}
  };
  const retry=async()=>{
    if(submitting.current||!source||discardAccepted.current)return;
    submitting.current=true;setBusy(true);
    try{await apiClient.retryNormalization(materialId,source.normalization_id);setReload(n=>n+1);}
    catch(e){setError(errorMessage(e));}
    finally{submitting.current=false;setBusy(false);}
  };
  if(!data&&!error)return <StateView title="正在讀取教材轉換狀態" description="已上傳的原檔會保留，重新整理不會重複轉換。" tone="loading" live />;
  const converting = source?.status === "pending" || source?.status === "running";
  const failed = source?.status === "failed";
  const ready = source?.status === "ready";
  const analysisStarted = !!run && run.status !== "failed";
  const analysisComplete = run?.status === "succeeded" || run?.status === "partial";
  const mascot = !removing && failed ? "failure-confused" : !removing && ready ? "success-jump" : "processing-laptop";
  return <section className="task-page source-page">
    <header className="upload-hero">
      <img src={`/assets/studydy/${mascot}.png`} alt="" />
      <div><h1>教材轉換</h1><p>將教材轉換為 PDF，確認內容後即可開始分析。</p></div>
    </header>
    {error && <div role="alert" className="surface source-request-error">
      <p className="form-error">{error}</p>
      <button className="secondary-button" onClick={() => setReload(n => n + 1)}>重新讀取</button>
    </div>}
    <div className="upload-layout">
      <div>
        {source ? <section className="surface processing-card source-card">
          <h2>轉換狀態</h2>
          <div className="chosen-file source-file">
            <span className="library-file-icon"><Icon name="file" size={24} /></span>
            <div><strong>{source.original_name}</strong><small>{material && `${formatFileSize(material.size_bytes)} · `}原始教材已保留</small></div>
          </div>
          <div className={`source-status${!removing && failed ? " is-failed" : !removing && ready ? " is-ready" : ""}`}>
            <span className="completion-icon source-status-icon" aria-hidden="true">
              {removing || converting ? <span className="processing-status-indicator" /> : <Icon name={failed ? "warning" : "check"} />}
            </span>
            <div>
              <p role="status">{removing ? "正在刪除教材…" : source.status === "pending" ? "等待轉換" : source.status === "running" ? "正在轉換成 PDF…" : failed ? "轉換失敗，原檔仍保留。" : "轉換完成"}</p>
              <p className="source-status-description">{removing ? "刪除完成後會自動返回教材庫。" : failed ? "可能是檔案格式、加密、內容或轉換工具限制。可重試轉換，或改上傳 PDF。" : ready ? "預覽 PDF 並確認文字與版面後，即可開始分析。" : "轉換期間可離開此頁，稍後從教材庫繼續查看。"}</p>
            </div>
          </div>
          <div className="source-file-actions">
            {source.normalized_artifact_id && <a className="secondary-button" href={removing ? undefined : apiClient.sourceArtifactUrl(source.normalized_artifact_id)} target="_blank" rel="noopener noreferrer"><Icon name="eye" />預覽轉換後 PDF（{source.page_count} 頁）</a>}
            <a className="text-button" href={removing ? undefined : `/v2/artifacts/${source.original_artifact_id}`} target="_blank" rel="noopener noreferrer">下載原檔</a>
          </div>
          {!removing && (failed || ready) && <div className="source-next-action">
            {failed ? <button className="primary-button" disabled={busy} onClick={() => void retry()}><Icon name="refresh" />{busy ? "正在重試…" : "重試轉換"}</button> : run && run.status !== "failed" ? <button className="primary-button" onClick={() => writeRoute({name:"material-run",materialId,runId:run.run_id})}>查看處理結果<Icon name="chevron-right" /></button> : <button className="primary-button" disabled={busy} onClick={() => void start()}>{busy ? "正在建立分析…" : "開始分析教材"}<Icon name="chevron-right" /></button>}
          </div>}
          {failed && source.error_code && <details className="processing-technical source-technical"><summary>查看錯誤資訊</summary><code>{source.error_code}</code></details>}
        </section> : <div className="surface processing-card">
          <StateView variant="embedded" title="尚未收到原檔" description="請回上傳頁重新選檔，或刪除這份空白教材。" tone="empty" icon="file" />
        </div>}
        <div className="source-footer">
          <button className="text-button" onClick={() => writeRoute({name:"materials"})}><Icon name="arrow-left" />返回教材庫</button>
          <MaterialRemoveControl inActionRow hasConversion apiClient={apiClient} materialId={materialId} onAccepted={state=>{if(state==="removed")writeRoute({name:"materials"});else {discardAccepted.current=true;setRemoving(true);setReload(n=>n+1);}}} />
        </div>
      </div>
      <aside className="surface guide-card source-guide" aria-label="教材轉換流程">
        <h2>從教材到知識地圖</h2>
        <ol>
          <li className={source ? "is-complete" : "is-active"} aria-current={!source ? "step" : undefined}><span aria-hidden="true">{source ? <Icon name="check" size={16} /> : "1"}</span><div><strong>上傳教材</strong><p>保留原始檔案，隨時可以下載。</p></div></li>
          <li className={analysisStarted ? "is-complete" : source ? "is-active" : undefined} aria-current={source && !analysisStarted ? "step" : undefined}><span aria-hidden="true">{analysisStarted ? <Icon name="check" size={16} /> : "2"}</span><div><strong>確認 PDF</strong><p>轉換完成後，先檢查文字與版面。</p></div></li>
          <li className={analysisComplete ? "is-complete" : analysisStarted ? "is-active" : undefined} aria-current={analysisStarted && !analysisComplete ? "step" : undefined}><span aria-hidden="true">{analysisComplete ? <Icon name="check" size={16} /> : "3"}</span><div><strong>開始分析教材</strong><p>由你確認開始，再建立知識地圖。</p></div></li>
        </ol>
        <div className="upload-requirements"><h3>轉換提醒</h3><p>PDF 為主要格式。其他格式自動轉為 PDF，轉換品質不保證。</p></div>
      </aside>
    </div>
  </section>;
}

import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, type StudydyApiClient } from "../api/client";
import type { EvidenceSourceView, EvidenceView } from "../api/contracts";

export function sourceLinks(evidence: EvidenceView[], resolver?: string): EvidenceView[] {
  // 多來源 resolver 保留區塊定位；直接開啟單 PDF 時，同頁只有同一個開啟動作。
  return [...new Map(evidence.map(item => [resolver ? item.evidence_id : item.page, item])).values()];
}

export function SourceButton({apiClient,artifactId,page,evidenceId,resolver,children,evidence}:{apiClient:StudydyApiClient;artifactId:string;page:number;evidenceId?:string;resolver?:string;children:ReactNode;evidence?:EvidenceView}) {
  const [source,setSource]=useState<EvidenceSourceView|null>(null);
  const [error,setError]=useState<string|null>(null);const [busy,setBusy]=useState(false);
  const dialog=useRef<HTMLDialogElement>(null);const opener=useRef<HTMLButtonElement>(null);
  useEffect(()=>{if(source)dialog.current?.showModal();},[source]);
  const open=async()=>{
    if(!resolver){window.open(apiClient.sourceArtifactUrl(artifactId,page),"_blank","noopener,noreferrer");return;}
    if(!evidenceId||busy)return;setBusy(true);setError(null);
    try{setSource(await apiClient.resolveEvidence(resolver,evidenceId));}catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  return <>
    <button ref={opener} className="text-button" type="button" disabled={busy} onClick={()=>void open()} aria-haspopup={resolver?"dialog":undefined}>{resolver?busy?"正在讀取來源…":evidence?.source_name?`${evidence.source_name} · PDF 第 ${evidence.normalized_page} 頁`:`查看第 ${page} 頁來源`:children}</button>
    {error&&<p role="alert" className="form-error">{error}</p>}
    {source&&<dialog ref={dialog} className="source-dialog" aria-label="教材來源" onCancel={event=>{event.preventDefault();event.stopPropagation();dialog.current?.close();}} onKeyDown={event=>{if(event.key==="Escape")event.stopPropagation();}} onClose={()=>{setSource(null);opener.current?.focus();}}>
      <h2>{source.original_name}</h2><p>{source.label}</p>
      {source.accuracy!=="exact"&&<p>原始文件的位置{source.accuracy==="ambiguous"?"可能對應多處":"無法精確對應"}，請以轉換後 PDF 回查。</p>}
      {source.format!=="pdf"&&<p>此 PDF 由系統自動轉換，轉換品質不保證。</p>}
      <div className="state-actions"><a className="primary-button" href={source.preview_url} target="_blank" rel="noopener noreferrer">開啟 PDF 來源頁</a><a className="secondary-button" href={source.original_url} target="_blank" rel="noopener noreferrer">下載原檔</a><button className="secondary-button" type="button" onClick={()=>dialog.current?.close()}>關閉</button></div>
    </dialog>}
  </>;
}

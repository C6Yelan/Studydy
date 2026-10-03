import { useState } from 'react';
import type { StudydyApiClient } from '../../api/client';
import { ResearchPanel } from './ResearchPanel';
import { VoicePanel } from './VoicePanel';
import './styles.css';
export function MaterialTools({api,materialId}:{api:StudydyApiClient;materialId:string}){
 const [open,setOpen]=useState<"voice"|"research"|null>(null);
 return <div className="material-tools"><button className="secondary-button" onClick={()=>setOpen(v=>v==="voice"?null:"voice")} aria-expanded={open==="voice"}>語音問答</button><button className="secondary-button" onClick={()=>setOpen(v=>v==="research"?null:"research")} aria-expanded={open==="research"}>補充學習</button>
 {open&&<section className="material-tool-panel" aria-label={open==="voice"?"教材語音問答":"補充學習"}><header><h2>{open==="voice"?"教材語音問答":"補充學習"}</h2><button className="secondary-button" onClick={()=>setOpen(null)}>關閉</button></header>{open==="voice"?<VoicePanel api={api} materialId={materialId}/>:<ResearchPanel api={api} materialId={materialId} onNavigate={()=>setOpen(null)}/>}</section>}</div>;
}

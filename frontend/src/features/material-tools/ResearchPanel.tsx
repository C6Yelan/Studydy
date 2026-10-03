import { useEffect,useRef,useState } from 'react';
import { errorMessage,type StudydyApiClient } from '../../api/client';
import { writeRoute } from '../../app/routes';

type Candidate={id:string;kind:string;title:string;authors:string;year:number|null;url:string;doi:string|null;license:string;license_url:string|null;version:string;eligible:boolean;reason:string;state:string;error_code?:string;acquisition?:{license_text?:string;acquired_at:string}};
type Research={research_id:string;query:string;mode:string;status:string;candidates:Candidate[];selection:string[];cursor:string|null;error_code:string|null;run_id:string|null;is_current_revision?:boolean;run?:{status:string;error_code:string|null}};
const active=new Set(['searching','acquiring','normalizing']);
const labels:Record<string,string>={searching:'搜尋中',selecting:'請選擇補充來源',acquiring:'正在取得選取來源',normalizing:'正在轉換文件',ready:'可加入教材',submitted:'已送入教材分析',failed:'部分處理未完成',cancelled:'已停止'};
const states:Record<string,string>={candidate:'候選來源',normalizing:'轉換中',ready:'文件已準備好',failed:'取得失敗'};
const reasons:Record<string,string>={RESEARCH_SEARCH_FAILED:'搜尋暫時失敗，請稍後重試。',RESEARCH_DOWNLOAD_FAILED:'來源目前無法下載。',RESEARCH_LICENSE_UNCONFIRMED:'無法核對全文授權。',RESEARCH_IDENTITY_UNCONFIRMED:'下載文件與搜尋紀錄的身分尚無法核對。',RESEARCH_FORMAT_UNSUPPORTED:'目前無法處理此文件格式。',DUPLICATE_SOURCE:'這份文件已存在於教材。',RESEARCH_ITEMS_FAILED:'部分來源未完成。可重試，或只勾選已準備好的來源繼續。',RESEARCH_OFFICIAL_UNAVAILABLE:'官方文件搜尋暫時失敗；學術搜尋結果已保留。'};

export function ResearchPanel({api,materialId,onNavigate}:{api:StudydyApiClient;materialId:string;onNavigate:()=>void}){
 const [query,setQuery]=useState(''),[mode,setMode]=useState('review'),[list,setList]=useState<Research[]>([]),[id,setId]=useState<string|null>(null),[view,setView]=useState<Research|null>(null),[selected,setSelected]=useState<string[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState(''),[confirmed,setConfirmed]=useState(false);
 const searchIntent=useRef<{body:string;key:string}|null>(null);
 const alive=useRef(true),current=useRef<string|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 const refresh=async()=>{const r=await api.studyTools<{researches:Research[]}>(`/v1/materials/${materialId}/research`);if(alive.current)setList(r.researches);};
 useEffect(()=>{void refresh().catch(e=>setError(errorMessage(e)));},[api,materialId]);
 useEffect(()=>{current.current=id;setView(null);if(!id)return;let stop=false;let timer:ReturnType<typeof setTimeout>;
 const read=async()=>{try{const r=await api.studyTools<Research>(`/v1/research/${id}`);if(stop)return;setView(r);if(active.has(r.status)||(r.status==='submitted'&&r.run&&['pending','running'].includes(r.run.status)))timer=setTimeout(read,2000);}catch(e){if(!stop)setError(errorMessage(e));}};
 void read();return()=>{stop=true;clearTimeout(timer);};},[api,id,busy]);
 const perform=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();await refresh();}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 const action=(action:string,ids?:string[])=>perform(async()=>{const r=await api.studyTools<Research>(`/v1/research/${id}/actions`,{action,...(ids?{selected:ids}:{})});if(alive.current&&current.current===r.research_id){setView(r);setConfirmed(false);}});
 const working=busy||!!view&&active.has(view.status);
 return <div className="research-panel"><p>從學術論文與官方教學找補充資料。選好來源後再加入這份教材。</p>
 <label>學習目的<select aria-label="學習目的" value={mode} onChange={e=>setMode(e.target.value)}><option value="review">課程複習</option><option value="self-study">自主學習</option></select></label>
 <label>想多了解什麼？<textarea aria-label="想多了解什麼？" rows={3} maxLength={1000} value={query} onChange={e=>setQuery(e.target.value)} placeholder={mode==='review'?'例如：補充 TCP 與 UDP 的差異，幫助理解課堂內容':'例如：深入了解網路傳輸與實際應用'}/></label>
 <button className="primary-button" disabled={busy||!query.trim()} onClick={()=>void perform(async()=>{const body=JSON.stringify({query,mode});if(searchIntent.current?.body!==body)searchIntent.current={body,key:crypto.randomUUID()};const r=await api.studyTools<Research>(`/v1/materials/${materialId}/research`,{query,mode},searchIntent.current.key);searchIntent.current=null;if(alive.current){setId(r.research_id);setSelected([]);setConfirmed(false);}})}>搜尋補充資料</button>
 <label>已保存的搜尋<select aria-label="已保存的搜尋" value={id??''} disabled={busy} onChange={e=>{const r=list.find(x=>x.research_id===e.target.value);setId(e.target.value||null);setSelected(r?.selection??[]);setConfirmed(false);setError('');}}><option value="">選擇搜尋紀錄</option>{list.map(r=><option key={r.research_id} value={r.research_id}>{r.query}</option>)}</select></label>
 {error&&<p role="alert" className="form-error">{error}</p>}
 {view&&<><h3>{view.query}</h3><p role="status">{labels[view.status]} · {view.mode==='review'?'課程複習':'自主學習'}</p>
 {view.error_code&&<p>{reasons[view.error_code]??'處理未完成，已保存取得的結果。'}</p>}
 {view.is_current_revision===false&&view.status!=='submitted'&&<p>教材已更新，這次搜尋不能直接加入舊版本。請以目前教材重新搜尋。</p>}
 <p className="research-scope">學術搜尋由 OpenAlex 提供；官方教學目前支援 Python 官方文件與 MDN Web Docs。未確認全文授權的來源可開啟查看，但不能自動匯入。</p>
 <div className="state-actions"><button className="text-button" disabled={working||view.status==='submitted'} onClick={()=>setSelected(view.candidates.filter(c=>c.eligible).map(c=>c.id))}>全選可用來源</button><button className="text-button" disabled={working} onClick={()=>setSelected([])}>取消全選</button><span>已選 {selected.length} 份</span></div>
 <div className="research-candidates">{view.candidates.map(c=><article key={c.id}><label><input type="checkbox" aria-label={`選取 ${c.title}`} disabled={!c.eligible||working||view.status==='submitted'} checked={selected.includes(c.id)} onChange={e=>{setSelected(v=>e.target.checked?[...v,c.id]:v.filter(x=>x!==c.id));setConfirmed(false);}}/><strong>{c.title}</strong></label>
 <p>{c.kind==='official'?'官方教學':'學術論文'} · {c.authors}{c.year?` · ${c.year}`:''}</p><p>{c.version} · {c.license} · {states[c.state]}</p><p>{c.error_code?reasons[c.error_code]??'取得失敗，可重試。':c.reason}</p>
 <div className="state-actions"><a href={c.url.startsWith('https://')?c.url:undefined} target="_blank" rel="noreferrer">查看原始來源</a>{c.license_url&&<a href={c.license_url} target="_blank" rel="noreferrer">授權說明</a>}</div>
 {c.acquisition?.license_text&&<details><summary>已保存的授權聲明</summary><pre>{c.acquisition.license_text}</pre></details>}
 </article>)}</div>
 {!view.candidates.length&&!active.has(view.status)&&<p>沒有找到來源。可以換個主題或英文關鍵字再搜尋。</p>}
 <div className="state-actions">{view.status==='selecting'&&view.cursor&&<button className="secondary-button" disabled={busy} onClick={()=>void action('more')}>載入更多論文</button>}
 {['selecting','ready','failed'].includes(view.status)&&<button className="secondary-button" disabled={working||!selected.length} onClick={()=>void action('acquire',selected)}>取得選取的 {selected.length} 份來源</button>}
 {active.has(view.status)&&<button className="secondary-button" disabled={busy} onClick={()=>void action('cancel')}>停止處理</button>}
 {['failed','cancelled'].includes(view.status)&&<button className="secondary-button" disabled={busy} onClick={()=>void action('retry')}>重試未完成部分</button>}</div>
 {view.status==='ready'&&view.selection.length===selected.length&&view.selection.every(x=>selected.includes(x))&&<div className="research-confirm"><p>準備加入 {view.selection.length} 份補充資料。加入後會更新教材與知識地圖，<strong>無法撤回新增內容</strong>。</p><label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我了解加入後不可逆</label><button className="primary-button" disabled={busy||!confirmed||view.is_current_revision===false} onClick={()=>void perform(async()=>{const r=await api.studyTools<Research>(`/v1/research/${id}/submit`,{confirmed:true});if(alive.current)setView(r);})}>確認加入教材</button></div>}
 {view.run_id&&<><p>{view.run?.status==='cancelled'?'教材分析已取消；已取得的來源仍保留，可到來源管理重新開始。':view.run?.status==='failed'?'教材分析未完成，可前往進度頁重試。':view.run?.status==='succeeded'||view.run?.status==='partial'?'補充資料已完成分析。':'已開始分析，可離開頁面，稍後回來查看。'}</p><button className="primary-button" onClick={()=>{writeRoute({name:'material-run',materialId,runId:view.run_id!});onNavigate();}}>查看教材分析</button></>}
 </>}
 </div>;
}

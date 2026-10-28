import { CreationLearningSettings, type CreationSettings } from "../study-session/CreationLearningSettings";
import { MaterialNameField } from "../../ui/MaterialNameField";
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {BackLink} from '../../ui/BackLink';
import {errorMessage,isNotFound,type StudydyApiClient} from '../../api/client';
import {NotFoundView} from '../../ui/NotFoundView';
import type {MaterialProcessingRunView} from '../../api/contracts';
import {ProcessingProgress} from '../material-flow/ProcessingProgress';
import {ProcessingTimeline} from '../material-flow/ProcessingTimeline';
import {MaterialRunFailure} from '../material-flow/MaterialRunFailure';
import {MaterialRemoveControl} from '../material-flow/MaterialRemoveControl';
import {FlowFooter,FlowHeader,FlowStage,LearningSteps,topicSteps} from '../material-flow/Flow';
import {automaticPollIntervalMs,materialElapsedLabel,materialProgressStageLabel,materialProgressStages} from '../material-flow/material-flow';
import {writeRoute} from '../../app/routes';
import { Icon } from '../../ui/Icon';
import {ResearchProgress} from '../material-tools/ResearchProgress';
import {SourceCandidates,sourceReasons} from '../material-tools/SourceCandidates';
import type {Research} from '../material-tools/research';
import '../material-tools/styles.css';
import './styles.css';

type Scope={title:string;goals:string[];topics:string[]};
export type Topic={topic_id:string;material_id:string|null;request:string;proposal:Scope|null;version:number;status:string;research_id:string|null;research?:Research;error_code:string|null;learning_goal?:{target_date:string;target_percent:60|80|100}|null};
const active=new Set(['searching','acquiring','normalizing','ready']);
const stages:Record<string,string>={searching:'正在搜尋來源',selecting:'選擇想加入的來源',acquiring:'正在取得選取來源',normalizing:'正在轉換文件',ready:'正在建立教材分析',submitted:'已送入教材分析',failed:'處理未完成',cancelled:'已停止'};
const timelineStages=materialProgressStages.slice(0,-1).map(stage=>({label:materialProgressStageLabel(stage),icon:stage==='semantics'?'map' as const:'process' as const}));

/** 一列一個輸入框＋右側 X；Enter 跳到下一項，最後一項按 Enter 新增一項。 */
function ScopeList({label,itemLabel,addLabel,hint,items,onChange,busy,error}:{label:string;itemLabel:string;addLabel:string;hint?:string;items:string[];onChange:(items:string[])=>void;busy:boolean;error?:string}){
 const fields=useRef<(HTMLTextAreaElement|null)[]>([]),add=useRef<HTMLButtonElement>(null);
 const [focusIndex,setFocusIndex]=useState<number|null>(null);
 useEffect(()=>{if(focusIndex===null)return;(focusIndex<items.length?fields.current[focusIndex]:add.current)?.focus();setFocusIndex(null);},[focusIndex,items.length]);
 const count=items.filter(x=>x.trim()).length;
 return <fieldset className="topic-scope-list">
  <legend>{label}<small>{count} 項</small></legend>
  {hint&&<p className="topic-scope-hint">{hint}</p>}
  <ol>{items.map((item,index)=><li key={index}>
   <span className="topic-scope-index" aria-hidden="true">{index+1}</span>
   <textarea ref={node=>{fields.current[index]=node;}} rows={1} aria-label={`${itemLabel} ${index+1}`} value={item} disabled={busy}
    onChange={e=>onChange(items.map((value,i)=>i===index?e.target.value:value))}
    onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(index===items.length-1)onChange([...items,'']);setFocusIndex(index+1);}}}/>
   <button type="button" className="flow-icon-button" disabled={busy} aria-label={`移除${itemLabel} ${index+1}`} title="移除" onClick={()=>{onChange(items.filter((_,i)=>i!==index));setFocusIndex(items.length>1?Math.min(index,items.length-2):items.length);}}><Icon name="close" size={18}/></button>
  </li>)}</ol>
  <button ref={add} type="button" className="topic-scope-add" disabled={busy} aria-label={addLabel} onClick={()=>{onChange([...items,'']);setFocusIndex(items.length);}}><Icon name="plus" size={16}/>新增一項</button>
  {error&&<p className="form-error" role="alert">{error}</p>}
 </fieldset>;
}

function ScopeFields({draft,onChange,busy,request,supplemental,settings,actions}:{draft:Scope;onChange:(scope:Scope)=>void;busy:boolean;request:string;supplemental:boolean;settings?:ReactNode;actions:ReactNode}){
 const fields=useRef<(HTMLTextAreaElement|null)[]>([]),add=useRef<HTMLButtonElement>(null);
 const [focusIndex,setFocusIndex]=useState<number|null>(null);
 useEffect(()=>{if(focusIndex===null)return;(focusIndex<draft.topics.length?fields.current[focusIndex]:add.current)?.focus();setFocusIndex(null);},[focusIndex,draft.topics.length]);
 const hasGoals=draft.goals.some(x=>x.trim()),count=draft.topics.filter(x=>x.trim()).length;
 const totalLength=[...draft.goals,...draft.topics].reduce((sum,item)=>sum+item.length,0);
 return <div className="topic-scope-editor-layout">
  <section aria-label="觀念與想了解的細節">
   <ol className="topic-concept-grid">{draft.topics.map((topic,index)=>{
    const separator=topic.search(/[：:]/);
    const name=separator<0?topic:topic.slice(0,separator),detail=separator<0?'':topic.slice(separator+1);
    const update=(value:string)=>onChange({...draft,topics:draft.topics.map((item,i)=>i===index?value:item)});
    return <li className="topic-concept-card" key={index}>
     <div className="topic-concept-heading"><span className="topic-concept-number" aria-hidden="true">{index+1}</span>
      <textarea ref={node=>{fields.current[index]=node;}} className="topic-concept-name" rows={1} aria-label={`觀念名稱 ${index+1}`} placeholder="觀念名稱" value={name} disabled={busy}
       onChange={event=>update(event.target.value+(separator<0?'':topic.slice(separator)))}
       onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();if(index===draft.topics.length-1)onChange({...draft,topics:[...draft.topics,'']});setFocusIndex(index+1);}}}/>
      <button type="button" className="flow-icon-button" disabled={busy} aria-label={`移除主題 ${index+1}`} onClick={()=>{onChange({...draft,topics:draft.topics.filter((_,i)=>i!==index)});setFocusIndex(draft.topics.length>1?Math.min(index,draft.topics.length-2):draft.topics.length);}}><Icon name="close" size={18}/></button>
     </div>
     <label className="topic-concept-detail"><span>想了解的細節</span><textarea rows={3} aria-label={`想了解的細節 ${index+1}`} value={detail} disabled={busy}
      onChange={event=>update(name+(event.target.value?(separator<0?'：':topic[separator])+event.target.value:''))}/></label>
     {topic.length>500&&<p className="form-error" role="alert">觀念名稱與細節合計最多 500 字，請縮短後再確認。</p>}
    </li>;
   })}</ol>
   <button ref={add} type="button" className="topic-scope-add" disabled={busy} aria-label="新增主題" onClick={()=>{onChange({...draft,topics:[...draft.topics,'']});setFocusIndex(draft.topics.length);}}><Icon name="plus" size={16}/>新增觀念</button>
   {count===0&&<p className="form-error" role="alert">請至少保留一個要涵蓋的主題。</p>}
  </section>
  <aside className="topic-scope-review" aria-label="對照學習需求">
   <MaterialNameField maxLength={supplemental?180:40} label={supplemental?'這次補充的名稱':'教材名稱'} value={draft.title} disabled={busy} onChange={title=>onChange({...draft,title})}/>
   <section><h3>原始學習需求</h3><p className="topic-original-request">{request}</p></section>
   <ScopeList label="學習目標" itemLabel="學習目標" addLabel="新增學習目標" items={draft.goals} busy={busy} onChange={goals=>onChange({...draft,goals})} error={hasGoals?undefined:'請至少保留一個學習目標。'}/>
   {draft.goals.some(item=>item.length>500)&&<p className="form-error" role="alert">每個學習目標最多 500 字，請縮短後再確認。</p>}
   {totalLength>12000&&<p className="form-error" role="alert">觀念與學習目標合計最多 12000 字，請縮短後再確認。</p>}
   <div className="topic-scope-learning"><p className="topic-scope-count">將搜尋 {count} 個觀念</p>{settings}</div>
   <div className="topic-scope-confirm-actions">{actions}</div>
  </aside>
 </div>;
}

export function TopicPage({api,topicId}:{api:StudydyApiClient;topicId?:string}){
 const [request,setRequest]=useState(''),[view,setView]=useState<Topic|null>(null),[draft,setDraft]=useState<Scope|null>(null),[selected,setSelected]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
 const [learningSettings,setLearningSettings]=useState<CreationSettings>({goal:null});
 const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const [editingRequest,setEditingRequest]=useState(false);
 const [notFound,setNotFound]=useState(false);
 const viewLoaded=useRef(false);
 const requestLoaded=useRef(false);
 const alive=useRef(true),loadedVersion=useRef(''),selectionLoaded=useRef(''),createIntent=useRef<{body:string;key:string}|null>(null),approveIntent=useRef<{body:string;key:string}|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(!topicId)return;let stop=false;let timer:ReturnType<typeof setTimeout>;
 const read=async()=>{try{const v=await api.studyTools<Topic>(`/v1/topics/${topicId}`);if(stop)return;viewLoaded.current=true;setView(v);if(v.proposal&&loadedVersion.current!==`${v.topic_id}/${v.version}`){setDraft(v.material_id?v.proposal:{...v.proposal,title:v.proposal.title.slice(0,40)});setLearningSettings({goal:v.learning_goal?{date:v.learning_goal.target_date,percent:v.learning_goal.target_percent}:null});loadedVersion.current=`${v.topic_id}/${v.version}`;}if(!requestLoaded.current){setRequest(v.request);requestLoaded.current=true;}
 if(v.research&&selectionLoaded.current!==v.research.research_id){setSelected(v.research.selection);selectionLoaded.current=v.research.research_id;}
 if(v.status==='pending'||v.research&&(active.has(v.research.status)||v.research.run&&['pending','running'].includes(v.research.run.status)))timer=setTimeout(read,1800);
 }catch(e){if(!stop){if(isNotFound(e)&&!viewLoaded.current)setNotFound(true);else setError(errorMessage(e));}}};void read();return()=>{stop=true;clearTimeout(timer);};},[api,topicId,reload]);
 const perform=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();if(alive.current)setReload(v=>v+1);}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 const generate=()=>perform(async()=>{const payload={request,...(view?.material_id?{material_id:view.material_id}:{})};const body=JSON.stringify(payload);if(createIntent.current?.body!==body)createIntent.current={body,key:crypto.randomUUID()};const t=await api.studyTools<Topic>('/v1/topics',payload,createIntent.current.key);createIntent.current=null;if(alive.current)writeRoute({name:'topic',topicId:t.topic_id});});
 const actResearch=(action:string,ids?:string[])=>perform(async()=>{
  const result=await api.studyTools<Research>(`/v1/research/${view?.research_id}/actions`,{action,...(ids?{selected:ids}:{})});
  if(alive.current)setView(previous=>previous?.research_id===result.research_id?{...previous,research:result}:previous);
 });
 const work=view?.research,working=!!work&&active.has(work.status);
 useEffect(()=>{if(view?.material_id&&view.status==='approved'&&view.research_id)writeRoute({name:'research',materialId:view.material_id,researchId:view.research_id});},[view?.material_id,view?.status,view?.research_id]);
 const back=()=>writeRoute(view?.material_id?{name:'material-content',materialId:view.material_id,kind:'research'}:{name:'materials'});

 const [loadedRun,setLoadedRun]=useState<MaterialProcessingRunView|null>(null),[runError,setRunError]=useState(''),[runReload,setRunReload]=useState(0),[now,setNow]=useState(Date.now);
 // 已取消的教材可刪除；刪除受理但尚未清除完時持續讀取，讀不到教材後回到我的教材（同處理頁取消後的行為）。
 const [removing,setRemoving]=useState(false);
 const materialRun=loadedRun?.run_id===work?.run_id&&loadedRun?.material_id===work?.material_id?loadedRun:null;
 useEffect(()=>{
  if(!work?.material_id||!work.run_id)return;
  let stop=false;let timer:ReturnType<typeof setTimeout>;
  setLoadedRun(null);setRunError('');
  const read=async()=>{try{
   const next=await api.getMaterialRun(work.run_id!);if(stop)return;
   if(next.material_id!==work.material_id||next.run_id!==work.run_id)throw new Error('教材處理紀錄與目前主題不一致。');
   setLoadedRun(next);setRunError('');
   if(removing||['pending','running'].includes(next.status))timer=setTimeout(read,automaticPollIntervalMs);
  }catch(e){if(stop)return;if(removing&&isNotFound(e)){writeRoute({name:'materials'});return;}setRunError(errorMessage(e));}};
  void read();return()=>{stop=true;clearTimeout(timer);};
 },[api,work?.material_id,work?.run_id,runReload,removing]);
 const analyzing=!!materialRun&&['pending','running'].includes(materialRun.status);
 useEffect(()=>{if(!analyzing)return;setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[analyzing,materialRun?.run_id]);
 const completed=!removing&&!!materialRun&&['partial','succeeded'].includes(materialRun.status);
 const cancelled=!removing&&materialRun?.status==='cancelled',failed=!removing&&materialRun?.status==='failed';
 const cancelling=analyzing&&materialRun.cancel_requested_at!==null;
 const binding=completed?materialRun.output_binding:null;
 const hasMaterial=!!work?.material_id;
 const readOnlySources=hasMaterial&&!(work?.status==='failed'&&!work.run_id);
 const visibleSources=work?.candidates.filter(c=>(!readOnlySources||work.selection.includes(c.id)))??[];
 const inputStage=editingRequest||!view||['pending','failed','cancelled'].includes(view.status);
 const confirmingScope=!inputStage&&!!draft&&view?.status==='ready';
 const searchingSources=!inputStage&&view?.status==='approved'&&work?.status==='searching'&&!hasMaterial;
 const step=inputStage?0:view.status==='ready'?1:completed?4:hasMaterial?3:2;
 const errorNotice=error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={()=>{setError('');setReload(v=>v+1);}}>重新讀取</button></p>;
 if(notFound)return <NotFoundView title="找不到這個學習主題" description="這個主題可能已被刪除，或不屬於目前登入的帳號。" back={{label:'從主題建立教材',route:{name:'topics'}}}/>;
 const scopeReady=!!draft&&!!draft.title.trim()&&draft.topics.some(x=>x.trim())&&draft.goals.some(x=>x.trim())&&[...draft.topics,...draft.goals].every(item=>item.length<=500)&&[...draft.topics,...draft.goals].reduce((sum,item)=>sum+item.length,0)<=12000;
 const approve=()=>{if(!draft||!view)return;const clean={...draft,goals:draft.goals.filter(x=>x.trim()),topics:draft.topics.filter(x=>x.trim())};setDraft(clean);void perform(async()=>{const payload={expected_version:view.version,proposal:clean,...(!view.material_id?{learning_goal:learningSettings.goal?{target_date:learningSettings.goal.date,target_percent:learningSettings.goal.percent}:null}:{})};const body=JSON.stringify(payload);if(approveIntent.current?.body!==body)approveIntent.current={body,key:crypto.randomUUID()};const approved=await api.studyTools<Topic>(`/v1/topics/${view.topic_id}/approve`,payload,approveIntent.current.key);approveIntent.current=null;if(alive.current){if(view.material_id&&approved.research_id)writeRoute({name:'research',materialId:view.material_id,researchId:approved.research_id});else setView(approved);}});};
 const buildTitle=work?.run_id?(removing?'正在刪除教材…':completed?'教材與知識地圖已建立':cancelled?'已取消教材處理':failed?'教材處理失敗':cancelling?'正在取消教材處理':materialRun?.status==='pending'?'等待開始處理':materialRun?'正在分析教材':'正在讀取處理狀態'):work?.deleted?'查詢已刪除':'正在建立教材';
 const buildDescription=completed?`使用你選擇的 ${work?.selection.length??0} 份來源，已完成這份教材。`:removing?'分析已停止，正在刪除 PDF 與處理紀錄。':cancelling?'已收到取消要求，會在目前進行中的步驟完成後安全停止。':cancelled?'這次分析已停止。若不再需要這份教材，可以刪除 PDF 與處理紀錄。':'取得的來源會保留，可離開頁面，稍後回來查看進度。';
 if(work?.run_id) return <section className={`flow-page processing-page task-page${analyzing?' is-processing':''}`}>
  <FlowHeader back={<div className="run-material-return"><BackLink label="返回我的教材" onClick={back}/><span>{view?.proposal?.title}</span></div>}
    title={buildTitle} description={failed?undefined:buildDescription}/>
  <LearningSteps labels={topicSteps} current={completed?topicSteps.length:3}/>
  {errorNotice}
  <section aria-label="教材分析子流程">
   {runError&&<p className="form-error" role="alert">無法讀取處理狀態：{runError}<button className="text-button" onClick={()=>setRunReload(v=>v+1)}>重新讀取進度</button></p>}
   {!materialRun&&!runError&&<p role="status">正在讀取教材分析進度…</p>}
   {materialRun&&(removing?<section className="surface processing-card"><p role="status">正在停止處理並刪除這份教材，完成後會返回我的教材。</p></section>:<div className="processing-grid">
    {failed?<MaterialRunFailure apiClient={api} run={materialRun}/>:<section className="surface processing-card">
     {completed||cancelled?<>
      <p role="status" className={`flow-chip ${completed?'is-success':'is-muted'}`}>{completed?'處理完成':`停止於：${materialProgressStageLabel(materialRun.progress_stage)}`}{materialRun.total_pages!==null&&` · 已處理 ${materialRun.completed_pages} / ${materialRun.total_pages} 頁`}</p>
      {completed&&<p className="run-ai-notice">由 AI 整理，每個重點都可點開原文對照。</p>}
      <p>已耗時：{materialElapsedLabel(materialRun.created_at,Date.parse(materialRun.completed_at??materialRun.updated_at))}</p>
     </>:<ProcessingProgress run={materialRun} now={now}/>}
     <details className="topic-selected-sources"><summary>已選來源 · {work.selection.length} 份</summary><SourceCandidates candidates={visibleSources} selected={selected} disabled readOnly onChange={setSelected}/></details>
     <div className="run-actions">
      {(analyzing||cancelled)&&!removing&&<MaterialRemoveControl apiClient={api} materialId={materialRun.material_id} inActionRow
       actionLabel={analyzing?'取消並刪除教材':'刪除教材'}
       confirmationDescription={`${analyzing?'將先停止目前處理，再':'將'}刪除這份教材與取得的檔案。這次搜尋結果不會保留，這個主題也會從列表移除。此操作無法復原。`}
       onAccepted={state=>{if(state==='removed')writeRoute({name:'materials'});else setRemoving(true);}}/>}
      {completed&&work.candidates.some(c=>work.selection.includes(c.id)&&c.state==='failed')&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成來源</button>}
      {binding&&<button className="primary-button" onClick={()=>writeRoute({name:'knowledge-map',materialId:materialRun.material_id,runId:materialRun.run_id,structureRevision:binding.knowledge_structure_revision})}>開啟知識地圖<Icon name="chevron-right" size={16}/></button>}
     </div>
    </section>}
    <ProcessingTimeline stages={timelineStages} currentIndex={materialProgressStages.indexOf(materialRun.progress_stage)} activity={failed?'失敗':cancelled?'已停止':cancelling?'取消中':'進行中'}/>
   </div>)}
  </section>
 </section>;
 return <section className={`flow-page topic-page${confirmingScope?" topic-scope-confirmation":searchingSources?" topic-search-page":""}`}>
 <FlowHeader className={confirmingScope?'topic-scope-page-header':searchingSources?'topic-search-page-header':undefined} back={<BackLink label={view?.material_id?'返回補充學習':'返回我的教材'} onClick={back}/>} title={view?.status==='approved'?(view.proposal?.title??'搜尋教材來源'):view?.material_id?'確認補充學習需求':'從主題建立教材'} description={view?.status==='approved'?'依已確認的學習範圍查看來源與處理進度。':'先確認想查找的觀念與細節，再搜尋並核對來源。'}/>
 <LearningSteps labels={view?.material_id?[...topicSteps.slice(0,-1),'補充教材']:topicSteps} current={step}/>
 {(inputStage||view?.status!=='ready')&&errorNotice}
 {inputStage&&<FlowStage className="topic-request" icon="sparkle" title="你想學什麼？" description="可以是一個主題、一個問題，或想完成的學習目標。"
  footer={<FlowFooter
   secondary={<>
    {editingRequest&&<button className="secondary-button" disabled={busy} onClick={()=>setEditingRequest(false)}>返回目前規劃</button>}
    {view&&['pending','ready','failed'].includes(view.status)&&<button className="text-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'cancel',expected_version:view.version}))}>取消這個規劃</button>}
    {!request.trim()&&!view&&<span className="topic-disabled-hint">輸入想學的內容後，就能產生學習範圍。</span>}
   </>}
   primary={view?.status==='failed'?<button className="primary-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'retry',expected_version:view.version}))}>重試規劃</button>:<button className="primary-button" disabled={busy||!request.trim()||view?.status==='pending'||!!topicId&&!view} onClick={()=>void generate()}>{view?'重新規劃主題':'產生學習範圍'}<Icon name="chevron-right" size={16}/></button>}
  />}>
  <label className="topic-request-field"><span className="visually-hidden">想學的主題</span><textarea aria-label="想學的主題" disabled={busy||view?.status==='pending'||!!topicId&&!view} value={request} maxLength={1000} rows={4} onChange={e=>{requestLoaded.current=true;setRequest(e.target.value);}} placeholder="例如：我知道 IP 的用途，想了解 TCP 三次握手如何建立連線。"/></label>
  {view?.status==='pending'&&<div className="flow-wait" role="status"><span className="loading-ring"/><p>正在整理預計學習範圍；尚未搜尋外部來源。</p></div>}
  {view?.status==='failed'&&<p className="flow-notice">這次無法產生範圍，請重試。</p>}
  {view?.status==='cancelled'&&<p className="flow-notice">這個規劃已停止，可以建立新主題。</p>}
 </FlowStage>}
 {!inputStage&&draft&&view.status==='ready'&&<FlowStage className="topic-scope" icon="edit" title="確認學習範圍" description="直接修改每一項觀念與細節；確認前不會搜尋外部來源。">
  <ScopeFields draft={draft} onChange={setDraft} busy={busy} request={view.request} supplemental={!!view.material_id} settings={!view.material_id&&<CreationLearningSettings value={learningSettings} today={today} disabled={busy} onChange={setLearningSettings}/>} actions={<>
   <button className="primary-button" disabled={busy||!scopeReady} onClick={approve}>確認範圍並搜尋<Icon name="chevron-right" size={16}/></button>
   <button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>修改需求</button>
  </>}/>
  {errorNotice}
 </FlowStage>}
 {!inputStage&&view?.status==='approved'&&work&&<FlowStage className={`topic-sources${!hasMaterial?' is-selecting':''}`} icon={hasMaterial?(completed?'check':failed?'warning':'map'):'search'}
  tone={completed?'success':failed?'failure':cancelled?'attention':undefined}
  title={hasMaterial?buildTitle:'選擇教材來源'}
  description={hasMaterial?buildDescription:'勾選適合的論文、教學或網頁來源，再建立知識地圖。'}
  aside={!hasMaterial&&<span className="flow-chip">{stages[work.status]}</span>}
  footer={<FlowFooter
   secondary={<>
    {!work.deleted&&<>
     {work.status==='selecting'&&!hasMaterial&&!work.approved_scope&&<button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>重新確認搜尋範圍</button>}
     {work.status==='failed'&&!work.run_id&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成部分</button>}
     {working&&work.status!=='ready'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('cancel')}>{work.status==='searching'?'停止搜尋':'停止處理'}</button>}
     {work.status==='cancelled'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>接續處理</button>}
     {!hasMaterial&&!working&&<span>已選 {selected.length} 份來源</span>}
    </>}
   </>}
   primary={<>
    {!work.deleted&&!hasMaterial&&(work.status==='selecting'||work.status==='failed'&&work.error_code==='RESEARCH_CONTEXT_TOO_LARGE'&&work.progress?.stage==='following_up')&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/material`,{selected}))}>建立教材與知識地圖<Icon name="chevron-right" size={16}/></button>}
    {!work.deleted&&work.status==='failed'&&hasMaterial&&!work.run_id&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void actResearch('acquire',selected)}>使用選取來源繼續建立</button>}
   </>}
  />}>
  <div className={work.status==='searching'?"topic-search-scope":"topic-approved-summary"}>
   <span className="topic-approved-icon" aria-hidden="true"><Icon name="book" size={18}/></span>
   <div>{work.status!=='searching'&&<strong>{view.proposal?.title}</strong>}<details><summary>已確認的學習範圍</summary><dl><dt>涵蓋主題</dt><dd><ul>{view.proposal?.topics.map((topic,i)=><li key={i}>{topic}</li>)}</ul></dd><dt>學習目標</dt><dd><ul>{view.proposal?.goals.map((goal,i)=><li key={i}>{goal}</li>)}</ul></dd></dl></details></div>
   <button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>重新規劃</button>
  </div>
  {work.deleted&&<p className="flow-notice">查詢紀錄已刪除，已建立的教材與來源仍可查看。</p>}
  {work.error_code&&<p className="flow-notice">{sourceReasons[work.error_code]??'目前無法完成，已取得資料會保留。'}</p>}
  {work.status==='searching'&&!work.progress&&<div className="flow-wait" role="status"><span className="loading-ring"/><p>正在搜尋論文與官方教學。</p></div>}
  {work.progress&&!hasMaterial&&<ResearchProgress progress={work.progress} running={work.status==='searching'} candidates={work.candidates}/>}
  {(!work.progress||work.status!=='searching')&&(hasMaterial&&readOnlySources?<details className="topic-selected-sources"><summary>已選來源 · {work.selection.length} 份</summary>
   <SourceCandidates candidates={visibleSources} selected={selected} disabled readOnly onChange={setSelected}/>
  </details>:visibleSources.length>0&&<SourceCandidates candidates={visibleSources} selected={selected} disabled={busy||working||readOnlySources} readOnly={readOnlySources} onChange={setSelected}/>)}
  {!work.candidates.length&&work.status==='selecting'&&<p className="tool-empty">{work.progress?'':'沒有找到可加入的來源，'}可用「重新規劃」調整學習範圍後再搜尋。</p>}
 </FlowStage>}
 </section>;
}

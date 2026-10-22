import {useEffect,useRef,useState} from 'react';
import {BackLink} from '../../ui/BackLink';
import {errorMessage,isNotFound,type StudydyApiClient} from '../../api/client';
import {NotFoundView} from '../../ui/NotFoundView';
import type {MaterialProcessingRunView} from '../../api/contracts';
import {ProcessingProgress} from '../material-flow/ProcessingProgress';
import {ProcessingTimeline} from '../material-flow/ProcessingTimeline';
import {MaterialRunStartControl} from '../material-flow/MaterialRunStartControl';
import {FlowFooter,FlowHeader,FlowStage,LearningSteps} from '../material-flow/Flow';
import {automaticPollIntervalMs,materialElapsedLabel,materialFailureMessage,materialProgressStageLabel,materialProgressStages} from '../material-flow/material-flow';
import {writeRoute} from '../../app/routes';
import { Icon } from '../../ui/Icon';
import {ResearchProgress} from '../material-tools/ResearchProgress';
import {SourceCandidates,sourceReasons} from '../material-tools/SourceCandidates';
import type {Research} from '../material-tools/research';
import '../material-tools/styles.css';
import './styles.css';

type Scope={title:string;goals:string[];topics:string[]};
type Work=Research&{material_id:string|null;run?:{status:string;error_code:string|null;output_binding?:{knowledge_structure_revision:string}|null}};
export type Topic={topic_id:string;material_id:string|null;request:string;proposal:Scope|null;version:number;status:string;research_id:string|null;research?:Work;error_code:string|null};
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
   <textarea ref={node=>{fields.current[index]=node;}} rows={1} aria-label={`${itemLabel} ${index+1}`} value={item} disabled={busy} maxLength={1000}
    onChange={e=>onChange(items.map((value,i)=>i===index?e.target.value:value))}
    onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(index===items.length-1)onChange([...items,'']);setFocusIndex(index+1);}}}/>
   <button type="button" className="flow-icon-button" disabled={busy} aria-label={`移除${itemLabel} ${index+1}`} title="移除" onClick={()=>{onChange(items.filter((_,i)=>i!==index));setFocusIndex(items.length>1?Math.min(index,items.length-2):items.length);}}><Icon name="close" size={18}/></button>
  </li>)}</ol>
  <button ref={add} type="button" className="topic-scope-add" disabled={busy} aria-label={addLabel} onClick={()=>{onChange([...items,'']);setFocusIndex(items.length);}}><Icon name="plus" size={16}/>新增一項</button>
  {error&&<p className="form-error" role="alert">{error}</p>}
 </fieldset>;
}

function ScopeFields({draft,onChange,busy}:{draft:Scope;onChange:(scope:Scope)=>void;busy:boolean}){
 const hasGoals=draft.goals.some(x=>x.trim()),hasTopics=draft.topics.some(x=>x.trim());
 return <div className="topic-scope-fields">
  <label className="topic-scope-name"><span>教材名稱</span>
   <input aria-label="教材名稱" disabled={busy} value={draft.title} maxLength={180} aria-invalid={!draft.title.trim()} aria-describedby={!draft.title.trim()?'scope-title-error':undefined} onChange={e=>onChange({...draft,title:e.target.value})}/>
  </label>
  {!draft.title.trim()&&<p id="scope-title-error" className="form-error" role="alert">請填寫教材名稱。</p>}
  <ScopeList label="觀念與重點／細節" itemLabel="主題" addLabel="新增主題" hint="每項寫清楚想理解的觀念、具體問題與限制；確認後依這些內容查找來源。" items={draft.topics} busy={busy} onChange={topics=>onChange({...draft,topics})} error={hasTopics?undefined:'請至少保留一個要涵蓋的主題。'}/>
  <ScopeList label="學習目標" itemLabel="學習目標" addLabel="新增學習目標" items={draft.goals} busy={busy} onChange={goals=>onChange({...draft,goals})} error={hasGoals?undefined:'請至少保留一個學習目標。'}/>
 </div>;
}

export function TopicPage({api,topicId}:{api:StudydyApiClient;topicId?:string}){
 const [request,setRequest]=useState(''),[view,setView]=useState<Topic|null>(null),[draft,setDraft]=useState<Scope|null>(null),[selected,setSelected]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
 const [editingRequest,setEditingRequest]=useState(false);
 const [notFound,setNotFound]=useState(false);
 const viewLoaded=useRef(false);
 const [guidance,setGuidance]=useState('');
 useEffect(()=>setGuidance(''),[topicId]);
 const requestLoaded=useRef(false);
 const alive=useRef(true),loadedVersion=useRef(''),selectionLoaded=useRef(''),createIntent=useRef<{body:string;key:string}|null>(null),approveIntent=useRef<{body:string;key:string}|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{if(!topicId)return;let stop=false;let timer:ReturnType<typeof setTimeout>;
 const read=async()=>{try{const v=await api.studyTools<Topic>(`/v1/topics/${topicId}`);if(stop)return;viewLoaded.current=true;setView(v);if(v.proposal&&loadedVersion.current!==`${v.topic_id}/${v.version}`){setDraft(v.proposal);loadedVersion.current=`${v.topic_id}/${v.version}`;}if(!requestLoaded.current){setRequest(v.request);requestLoaded.current=true;}
 if(v.research&&selectionLoaded.current!==v.research.research_id){setSelected(v.research.selection);selectionLoaded.current=v.research.research_id;}
 if(v.status==='pending'||v.research&&(active.has(v.research.status)||v.research.run&&['pending','running'].includes(v.research.run.status)))timer=setTimeout(read,1800);
 }catch(e){if(!stop){if(isNotFound(e)&&!viewLoaded.current)setNotFound(true);else setError(errorMessage(e));}}};void read();return()=>{stop=true;clearTimeout(timer);};},[api,topicId,reload]);
 const perform=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();if(alive.current)setReload(v=>v+1);}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 const generate=()=>perform(async()=>{const payload={request,...(view?.material_id?{material_id:view.material_id}:{})};const body=JSON.stringify(payload);if(createIntent.current?.body!==body)createIntent.current={body,key:crypto.randomUUID()};const t=await api.studyTools<Topic>('/v1/topics',payload,createIntent.current.key);createIntent.current=null;if(alive.current)writeRoute({name:'topic',topicId:t.topic_id});});
 const actResearch=(action:string,ids?:string[])=>perform(async()=>{
  const result=await api.studyTools<Work>(`/v1/research/${view?.research_id}/actions`,{action,...(ids?{selected:ids}:{}),...(action==='more'&&guidance.trim()?{guidance:guidance.trim()}:{})});
  if(alive.current)setView(previous=>previous?.research_id===result.research_id?{...previous,research:result}:previous);
 });
 const work=view?.research,working=!!work&&active.has(work.status);
 useEffect(()=>{if(view?.material_id&&view.status==='approved'&&view.research_id)writeRoute({name:'research',materialId:view.material_id,researchId:view.research_id});},[view?.material_id,view?.status,view?.research_id]);
 const back=()=>writeRoute(view?.material_id?{name:'material-content',materialId:view.material_id,kind:'research'}:{name:'materials'});

 const [loadedRun,setLoadedRun]=useState<MaterialProcessingRunView|null>(null),[runError,setRunError]=useState(''),[runReload,setRunReload]=useState(0),[now,setNow]=useState(Date.now);
 const materialRun=loadedRun?.run_id===work?.run_id&&loadedRun?.material_id===work?.material_id?loadedRun:null;
 useEffect(()=>{
  if(!work?.material_id||!work.run_id)return;
  let stop=false;let timer:ReturnType<typeof setTimeout>;
  setLoadedRun(null);setRunError('');
  const read=async()=>{try{
   const next=await api.getMaterialRun(work.run_id!);if(stop)return;
   if(next.material_id!==work.material_id||next.run_id!==work.run_id)throw new Error('教材處理紀錄與目前主題不一致。');
   setLoadedRun(next);setRunError('');
   if(['pending','running'].includes(next.status))timer=setTimeout(read,automaticPollIntervalMs);
  }catch(e){if(!stop)setRunError(errorMessage(e));}};
  void read();return()=>{stop=true;clearTimeout(timer);};
 },[api,work?.material_id,work?.run_id,runReload]);
 const analyzing=!!materialRun&&['pending','running'].includes(materialRun.status);
 useEffect(()=>{if(!analyzing)return;setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer);},[analyzing,materialRun?.run_id]);
 const completed=!!materialRun&&['partial','succeeded'].includes(materialRun.status);
 const cancelled=materialRun?.status==='cancelled',failed=materialRun?.status==='failed';
 const cancelling=analyzing&&materialRun.cancel_requested_at!==null;
 const binding=completed?materialRun.output_binding:null;
 const hasMaterial=!!work?.material_id;
 const readOnlySources=hasMaterial&&!(work?.status==='failed'&&!work.run_id);
 const visibleSources=work?.candidates.filter(c=>(!readOnlySources||work.selection.includes(c.id)))??[];
 const inputStage=editingRequest||!view||['pending','failed','cancelled'].includes(view.status);
 const step=inputStage?0:view.status==='ready'?1:completed?4:hasMaterial?3:2;
 const errorNotice=error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={()=>{setError('');setReload(v=>v+1);}}>重新讀取</button></p>;
 if(notFound)return <NotFoundView title="找不到這個學習主題" description="這個主題可能已被刪除，或不屬於目前登入的帳號。" back={{label:'從主題建立教材',route:{name:'topics'}}}/>;
 const scopeReady=!!draft&&!!draft.title.trim()&&draft.topics.some(x=>x.trim())&&draft.goals.some(x=>x.trim());
 const approve=()=>{if(!draft||!view)return;const clean={...draft,goals:draft.goals.filter(x=>x.trim()),topics:draft.topics.filter(x=>x.trim())};setDraft(clean);void perform(async()=>{const body=JSON.stringify(clean);if(approveIntent.current?.body!==body)approveIntent.current={body,key:crypto.randomUUID()};const approved=await api.studyTools<Topic>(`/v1/topics/${view.topic_id}/approve`,{expected_version:view.version,proposal:clean},approveIntent.current.key);approveIntent.current=null;if(alive.current)writeRoute(view.material_id&&approved.research_id?{name:'research',materialId:view.material_id,researchId:approved.research_id}:{name:'materials'});});};
 const buildTitle=work?.run_id?(completed?'教材與知識地圖已建立':cancelled?'已取消教材處理':failed?'教材處理失敗':cancelling?'正在取消教材處理':materialRun?.status==='pending'?'等待開始處理':materialRun?'正在分析教材':'正在讀取處理狀態'):work?.deleted?'查詢已刪除':'正在建立教材';
 const buildDescription=completed?(materialRun?.status==='partial'?'知識地圖已建立，可先查看已整理的內容；部分內容未完整整理。':`使用你選擇的 ${work?.selection.length??0} 份來源，已完成這份教材。`):cancelling?'已收到取消要求，會在目前進行中的步驟完成後安全停止。':cancelled?'這次分析已停止。':failed?materialFailureMessage(materialRun.error_code??'MATERIAL_ANALYSIS_FAILED'):'取得的來源會保留，可離開頁面，稍後回來查看進度。';
 return <section className="flow-page topic-page">
 <FlowHeader back={<BackLink label={view?.material_id?'返回補充學習':'返回我的教材'} onClick={back}/>} title={view?.material_id?'確認補充學習需求':'從主題建立教材'} description="先確認想查找的觀念與細節，再搜尋並核對來源。"/>
 <LearningSteps labels={['學習需求','確認觀念與細節','選取來源',view?.material_id?'補充教材':'建立教材']} current={step}/>
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
 {!inputStage&&draft&&view.status==='ready'&&<FlowStage className="topic-scope" icon="edit" title="確認學習範圍" description="直接修改每一項觀念與細節；確認前不會搜尋外部來源。"
  footer={<FlowFooter
   secondary={<button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>修改學習需求</button>}
   primary={<button className="primary-button" disabled={busy||!scopeReady} onClick={approve}>確認範圍並搜尋<Icon name="chevron-right" size={16}/></button>}
  />}>
  <ScopeFields draft={draft} onChange={setDraft} busy={busy}/>
  {errorNotice}
 </FlowStage>}
 {!inputStage&&view?.status==='approved'&&work&&<FlowStage className={`topic-sources${!hasMaterial?' is-selecting':''}`} icon={hasMaterial?(completed?'check':failed?'warning':'map'):'search'}
  tone={completed?'success':failed?'failure':cancelled?'attention':undefined}
  title={hasMaterial?buildTitle:'選擇教材來源'}
  description={hasMaterial?buildDescription:'勾選適合的論文、教學或網頁來源，再建立知識地圖。'}
  aside={!hasMaterial&&<span className="flow-chip">{stages[work.status]}</span>}
  footer={<FlowFooter
   secondary={<>
    {work.status==='selecting'&&!hasMaterial&&work.approved_scope&&<details className="topic-guidance"><summary>調整補查方向</summary><label>補查方向（選填）<textarea rows={2} maxLength={1000} value={guidance} onChange={e=>setGuidance(e.target.value)} placeholder="說明想補足的機制、例子或來源類型" disabled={busy}/></label></details>}
    {!work.deleted&&<>
     {work.status==='submitted'&&['succeeded','partial'].includes(work.run?.status??'')&&work.candidates.some(c=>work.selection.includes(c.id)&&c.state==='failed')&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成來源</button>}
     {work.status==='selecting'&&!hasMaterial&&<button className="text-button" disabled={busy} onClick={()=>work.approved_scope?void actResearch('more'):setEditingRequest(true)}>{work.approved_scope?'依缺口繼續研究':'重新確認搜尋範圍'}</button>}
     {work.status==='failed'&&!work.run_id&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成部分</button>}
     {working&&work.status!=='ready'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('cancel')}>停止處理</button>}
     {work.status==='cancelled'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>接續處理</button>}
     {!hasMaterial&&!working&&<span>已選 {selected.length} 份來源</span>}
    </>}
   </>}
   primary={<>
    {!work.deleted&&work.status==='selecting'&&!hasMaterial&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/material`,{selected}))}>建立教材與知識地圖<Icon name="chevron-right" size={16}/></button>}
    {!work.deleted&&work.status==='failed'&&hasMaterial&&!work.run_id&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void actResearch('acquire',selected)}>使用選取來源繼續建立</button>}
    {failed&&materialRun&&<div className="topic-retry"><MaterialRunStartControl apiClient={api} materialId={materialRun.material_id} retryRun={{runId:materialRun.run_id,saved:!!materialRun.analysis_saved}}/></div>}
    {binding&&materialRun&&<button className="primary-button" onClick={()=>writeRoute({name:'knowledge-map',materialId:materialRun.material_id,runId:materialRun.run_id,structureRevision:binding.knowledge_structure_revision})}>開啟知識地圖<Icon name="chevron-right" size={16}/></button>}
   </>}
  />}>
  <div className="topic-approved-summary">
   <span className="topic-approved-icon" aria-hidden="true"><Icon name="book" size={18}/></span>
   <div><strong>{view.proposal?.title}</strong><details><summary>已確認的學習範圍</summary><dl><dt>涵蓋主題</dt><dd><ul>{view.proposal?.topics.map((topic,i)=><li key={i}>{topic}</li>)}</ul></dd><dt>學習目標</dt><dd><ul>{view.proposal?.goals.map((goal,i)=><li key={i}>{goal}</li>)}</ul></dd></dl></details></div>
   <button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>重新規劃</button>
  </div>
  {work.deleted&&<p className="flow-notice">查詢紀錄已刪除，已建立的教材與來源仍可查看。</p>}
  {work.error_code&&<p className="flow-notice">{sourceReasons[work.error_code]??'目前無法完成，已取得資料會保留。'}</p>}
  {work.status==='searching'&&!work.progress&&<div className="flow-wait" role="status"><span className="loading-ring"/><p>正在搜尋論文與官方教學。</p></div>}
  {work.run_id&&<section className={`topic-analysis${analyzing?' is-processing':''}`} aria-label="教材分析子流程">
   {runError&&<p className="form-error" role="alert">無法讀取處理狀態：{runError}<button className="text-button" onClick={()=>setRunReload(v=>v+1)}>重新讀取進度</button></p>}
   {!materialRun&&!runError&&<p role="status">正在讀取教材分析進度…</p>}
   {materialRun&&(analyzing?<div className="processing-grid"><div className="processing-card topic-analysis-progress"><ProcessingProgress run={materialRun} now={now} showSources={false}/></div><ProcessingTimeline stages={timelineStages} currentIndex={materialProgressStages.indexOf(materialRun.progress_stage)} activity={cancelling?'取消中':'進行中'}/></div>
   :<div className="topic-analysis-result">
    <p role="status" className={`flow-chip ${completed?(materialRun.status==='partial'?'is-attention':'is-success'):failed?'is-failure':'is-muted'}`}>{completed?(materialRun.status==='partial'?'部分結果可用':'處理完成'):`${failed?'最後記錄進度':'停止於'}：${materialProgressStageLabel(materialRun.progress_stage)}`}{materialRun.total_pages!==null&&` · 已處理 ${materialRun.completed_pages} / ${materialRun.total_pages} 頁`}</p>
    <p className="topic-analysis-elapsed"><Icon name="clock" size={15}/>已耗時：{materialElapsedLabel(materialRun.created_at,Date.parse(materialRun.completed_at??materialRun.updated_at))}</p>
    {failed&&materialRun.analysis_saved&&<p className="topic-analysis-note">已完成的處理進度已保存；重試會接續未完成的部分。</p>}
    {!completed&&<ProcessingTimeline stages={timelineStages} currentIndex={materialProgressStages.indexOf(materialRun.progress_stage)} activity={failed?'失敗':'已停止'}/>}
   </div>)}
  </section>}
  {work.progress&&!hasMaterial&&<ResearchProgress progress={work.progress} running={work.status==='searching'}/>}
  {(!work.progress||work.status!=='searching')&&(hasMaterial&&readOnlySources?<details className="topic-selected-sources"><summary>已選來源 · {work.selection.length} 份</summary>
   <SourceCandidates candidates={visibleSources} selected={selected} disabled readOnly onChange={setSelected}/>
  </details>:visibleSources.length>0&&<SourceCandidates candidates={visibleSources} selected={selected} disabled={busy||working||readOnlySources} readOnly={readOnlySources} onChange={setSelected}/>)}
  {work.progress&&work.status==='selecting'&&!visibleSources.length&&<p className="tool-empty">這次尚未整理出切題的正文來源，可查看未完成原因或依缺口繼續研究。</p>}
  {!work.candidates.length&&work.status==='selecting'&&<p className="tool-empty">沒有找到來源，請調整主題重新規劃。</p>}
 </FlowStage>}
 </section>;
}

import {useEffect,useRef,useState} from 'react';
import {errorMessage,type StudydyApiClient} from '../../api/client';
import type {MaterialProcessingRunView} from '../../api/contracts';
import {ProcessingProgress} from '../material-flow/ProcessingProgress';
import {ProcessingTimeline} from '../material-flow/ProcessingTimeline';
import {automaticPollIntervalMs,materialElapsedLabel,materialFailureMessage,materialProgressStageLabel,materialProgressStages} from '../material-flow/material-flow';
import {writeRoute} from '../../app/routes';
import { Icon } from '../../ui/Icon';
import { LearningSteps } from '../material-tools/LearningSteps';
import {SourceCandidates,sourceReasons} from '../material-tools/SourceCandidates';
import type {Research} from '../material-tools/research';
import '../material-tools/styles.css';
import './styles.css';

type Scope={title:string;level:string;goals:string[];topics:string[];exclude:string[]};
type Work=Research&{material_id:string|null;run?:{status:string;error_code:string|null;output_binding?:{knowledge_structure_revision:string}|null}};
type Topic={topic_id:string;request:string;proposal:Scope|null;version:number;status:string;research_id:string|null;research?:Work;error_code:string|null};
const active=new Set(['searching','acquiring','normalizing','ready']);
const stages:Record<string,string>={searching:'正在搜尋來源',selecting:'選擇想加入的來源',acquiring:'正在取得選取來源',normalizing:'正在轉換文件',ready:'正在建立教材分析',submitted:'已送入教材分析',failed:'處理未完成',cancelled:'已停止'};

const learningLevels=[
 {value:'入門',description:'第一次接觸：基本概念、先備知識與入門教學。'},
 {value:'有基礎',description:'已有基本認識：原理、實作教學與應用案例。'},
 {value:'深入',description:'研究細節：技術規格、限制、取捨與研究論文。'},
];

function ScopeFields({draft,onChange,busy}:{draft:Scope;onChange:(scope:Scope)=>void;busy:boolean}){
 const [editingTitle,setEditingTitle]=useState(false),[editingGoals,setEditingGoals]=useState(false),[editingTopic,setEditingTopic]=useState<number|null>(null);
 const titleButton=useRef<HTMLButtonElement>(null),goalsButton=useRef<HTMLButtonElement>(null),addButton=useRef<HTMLButtonElement>(null);
 const topicButtons=useRef<(HTMLButtonElement|null)[]>([]);
 const finish=(field:'title'|'goals')=>{
  if(field==='title'){setEditingTitle(false);titleButton.current?.focus();}
  if(field==='goals'){setEditingGoals(false);goalsButton.current?.focus();}
 };
 const originalLevel=useRef(draft.level);
 const customLevel=learningLevels.some(level=>level.value===originalLevel.current)?null:originalLevel.current;
 const hasGoals=draft.goals.some(x=>x.trim()),hasTopics=draft.topics.some(x=>x.trim());
 return <>
 <div className="topic-scope-name">
  <div className="topic-scope-row"><h3>{draft.title||'教材名稱'}</h3><button ref={titleButton} className="text-button" disabled={busy} aria-expanded={editingTitle} onClick={()=>editingTitle?finish('title'):setEditingTitle(true)}>編輯名稱</button></div>
  {editingTitle&&<div className="topic-inline-editor"><label className="tool-field">教材名稱<input autoFocus aria-label="教材名稱" disabled={busy} value={draft.title} maxLength={180} aria-invalid={!draft.title.trim()} aria-describedby={!draft.title.trim()?'scope-title-error':undefined} onChange={e=>onChange({...draft,title:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing&&draft.title.trim()){e.preventDefault();finish('title');}}}/></label><button className="secondary-button" disabled={busy||!draft.title.trim()} onClick={()=>finish('title')}>完成名稱編輯</button></div>}
  {!draft.title.trim()&&<p id="scope-title-error" className="form-error" role="alert">請填寫教材名稱。</p>}
 </div>
 <div className="topic-scope-level">
 <label className="tool-field">學習程度<select aria-label="學習程度" aria-describedby="scope-level-help" disabled={busy} value={draft.level} onChange={e=>onChange({...draft,level:e.target.value})}>
 {customLevel&&<option value={customLevel}>原有程度：{customLevel}</option>}
 {learningLevels.map(level=><option key={level.value} value={level.value}>{level.value}</option>)}
 </select></label>
 <p id="scope-level-help">{learningLevels.find(level=>level.value===draft.level)?.description??'保留原有程度描述作為搜尋依據，也可改選上方程度。'}<small>影響搜尋資料的深度，不影響正確性標準。</small></p>
 </div>
 <div className="topic-scope-grid">
 <section aria-labelledby="scope-topics-heading"><div className="topic-scope-row"><h3 id="scope-topics-heading">要涵蓋的主題</h3><span className="topic-scope-count">{draft.topics.filter(x=>x.trim()).length} 項</span></div>
 <ul className="topic-scope-items">{draft.topics.map((topic,index)=><li key={index}>
 {editingTopic===index?<input autoFocus aria-label={`主題 ${index+1}`} value={topic} disabled={busy} onChange={e=>onChange({...draft,topics:draft.topics.map((value,i)=>i===index?e.target.value:value)})} onKeyDown={e=>{if(e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();setEditingTopic(null);topicButtons.current[index]?.focus();}}}/>:<span>{topic.trim()?topic:'空白主題（確認時略過）'}</span>}
 <button ref={node=>{topicButtons.current[index]=node;}} className="text-button" disabled={busy} aria-label={`${editingTopic===index?'完成':'編輯'}主題 ${index+1}`} onClick={()=>{setEditingTopic(editingTopic===index?null:index);}}>{editingTopic===index?'完成':'編輯'}</button>
 <button className="text-button" disabled={busy} aria-label={`移除主題 ${index+1}`} onClick={()=>{onChange({...draft,topics:draft.topics.filter((_,i)=>i!==index)});setEditingTopic(null);addButton.current?.focus();}}>移除</button>
 </li>)}</ul>
 {!hasTopics&&<p className="form-error" role="alert">請至少保留一個要涵蓋的主題。</p>}
 <button ref={addButton} className="text-button" disabled={busy} onClick={()=>{setEditingTopic(draft.topics.length);onChange({...draft,topics:[...draft.topics,'']});}}>新增主題</button>
 </section>
 <section aria-labelledby="scope-goals-heading"><div className="topic-scope-row"><h3 id="scope-goals-heading">學習目標</h3><button ref={goalsButton} className="text-button" disabled={busy} aria-expanded={editingGoals} onClick={()=>editingGoals?finish('goals'):setEditingGoals(true)}>編輯學習目標</button></div>
 {editingGoals?<><label className="tool-field"><span className="visually-hidden">學習目標</span><textarea autoFocus aria-label="學習目標" rows={4} value={draft.goals.join('\n')} disabled={busy} aria-invalid={!hasGoals} aria-describedby={!hasGoals?'scope-goals-error':undefined} onChange={e=>onChange({...draft,goals:e.target.value.split('\n')})}/><small>每行一項</small></label><button className="secondary-button" disabled={busy||!hasGoals} onClick={()=>finish('goals')}>完成目標編輯</button></>:<ul className="topic-goals-summary">{draft.goals.filter(x=>x.trim()).map((goal,i)=><li key={i}><span aria-hidden="true">✓</span>{goal}</li>)}</ul>}
 {!hasGoals&&<p id="scope-goals-error" className="form-error" role="alert">請至少保留一個學習目標。</p>}
 </section>
 </div>
 <details className="topic-exclude"><summary>進階設定<span>本次不涵蓋{draft.exclude.some(x=>x.trim())?` · ${draft.exclude.filter(x=>x.trim()).length} 項`:'（選填）'}</span></summary><label className="tool-field">本次不涵蓋<textarea aria-label="本次不涵蓋" rows={3} disabled={busy} value={draft.exclude.join('\n')} onChange={e=>onChange({...draft,exclude:e.target.value.split('\n')})}/><small>每行一項；可以留白。</small></label></details>
 </>;
}

export function TopicPage({api,topicId}:{api:StudydyApiClient;topicId?:string}){
 const [request,setRequest]=useState(''),[list,setList]=useState<Topic[]>([]),[view,setView]=useState<Topic|null>(null),[draft,setDraft]=useState<Scope|null>(null),[selected,setSelected]=useState<string[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
 const [editingRequest,setEditingRequest]=useState(false);
 const requestLoaded=useRef(false);
 const alive=useRef(true),loadedVersion=useRef(''),selectionLoaded=useRef(''),createIntent=useRef<{body:string;key:string}|null>(null),approveIntent=useRef<{body:string;key:string}|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{let stop=false;void api.studyTools<{topics:Topic[]}>('/v1/topics').then(v=>{if(!stop)setList(v.topics);},e=>{if(!stop)setError(errorMessage(e));});return()=>{stop=true;};},[api,reload]);
 useEffect(()=>{if(!topicId)return;let stop=false;let timer:ReturnType<typeof setTimeout>;
 const read=async()=>{try{const v=await api.studyTools<Topic>(`/v1/topics/${topicId}`);if(stop)return;setView(v);setList(previous=>previous.map(t=>t.topic_id===v.topic_id&&(t.version!==v.version||t.status!==v.status||t.proposal?.title!==v.proposal?.title)?{...t,proposal:v.proposal,version:v.version,status:v.status}:t));if(v.proposal&&loadedVersion.current!==`${v.topic_id}/${v.version}`){setDraft(v.proposal);loadedVersion.current=`${v.topic_id}/${v.version}`;}if(!requestLoaded.current){setRequest(v.request);requestLoaded.current=true;}
 if(v.research&&selectionLoaded.current!==v.research.research_id){setSelected(v.research.selection);selectionLoaded.current=v.research.research_id;}
 if(v.status==='pending'||v.research&&(active.has(v.research.status)||v.research.run&&['pending','running'].includes(v.research.run.status)))timer=setTimeout(read,1800);
 }catch(e){if(!stop)setError(errorMessage(e));}};void read();return()=>{stop=true;clearTimeout(timer);};},[api,topicId,reload]);
 const perform=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();if(alive.current)setReload(v=>v+1);}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 const generate=()=>perform(async()=>{const body=JSON.stringify({request});if(createIntent.current?.body!==body)createIntent.current={body,key:crypto.randomUUID()};const t=await api.studyTools<Topic>('/v1/topics',{request},createIntent.current.key);createIntent.current=null;if(alive.current)writeRoute({name:'topic',topicId:t.topic_id});});
 const actResearch=(action:string,ids?:string[])=>perform(()=>api.studyTools(`/v1/research/${view?.research_id}/actions`,{action,...(ids?{selected:ids}:{})}));
 const work=view?.research,working=!!work&&active.has(work.status);
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
 const visibleSources=work?.candidates.filter(c=>!readOnlySources||work.selection.includes(c.id))??[];
 const inputStage=editingRequest||!view||['pending','failed','cancelled'].includes(view.status);
 const step=inputStage?0:view.status==='ready'?1:completed?4:hasMaterial?3:2;
 const errorNotice=error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={()=>{setError('');setReload(v=>v+1);}}>重新讀取</button></p>;
 return <section className="cards-page topic-page">
 <header className="cards-page-header"><div><h1>從主題建立教材</h1><p>告訴我們想學什麼，選擇資料後建立知識地圖。</p></div>
 <div className="topic-history-bar"><label className="tool-field"><span className="visually-hidden">已保存的主題</span><select aria-label="已保存的主題" value={topicId??''} onChange={e=>writeRoute(e.target.value?{name:'topic',topicId:e.target.value}:{name:'topics'})}><option value="">新的學習主題</option>{list.map(t=><option key={t.topic_id} value={t.topic_id}>{t.proposal?.title??t.request}</option>)}</select></label><button className="secondary-button" disabled={!topicId} onClick={()=>writeRoute({name:'topics'})}>新主題</button></div></header>
 <LearningSteps labels={['學習主題','確認範圍','選取來源','建立教材']} current={step}/>
 {(inputStage||view?.status!=='ready')&&errorNotice}
 {inputStage&&<section className="surface topic-section topic-request"><header className="tool-stage-heading"><div><h2>你想學什麼？</h2><p>可以是一個主題、一個問題，或想完成的學習目標。</p></div></header>
 <label className="tool-field"><span className="visually-hidden">想學的主題</span><textarea aria-label="想學的主題" disabled={busy||view?.status==='pending'||!!topicId&&!view} value={request} maxLength={1000} rows={3} onChange={e=>{requestLoaded.current=true;setRequest(e.target.value);}} placeholder="例如：我知道 IP 的用途，想了解 TCP 三次握手如何建立連線。"/></label>
 {view?.status==='pending'&&<div className="tool-wait" role="status"><span className="loading-ring"/><p>正在整理預計學習範圍；尚未搜尋外部來源。</p></div>}
 {view?.status==='failed'&&<p className="tool-notice">這次無法產生範圍，請重試。</p>}
 {view?.status==='cancelled'&&<p className="tool-notice">這個規劃已停止，可以建立新主題。</p>}
 <footer className="tool-stage-footer"><div className="tool-footer-secondary">
 {editingRequest&&<button className="secondary-button" disabled={busy} onClick={()=>setEditingRequest(false)}>返回目前規劃</button>}
 {view&&['pending','ready','failed'].includes(view.status)&&<button className="text-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'cancel',expected_version:view.version}))}>取消這個規劃</button>}
 </div>{view?.status==='failed'?<button className="primary-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'retry',expected_version:view.version}))}>重試規劃</button>:<button className="primary-button" disabled={busy||!request.trim()||view?.status==='pending'||!!topicId&&!view} onClick={()=>void generate()}>{view?'重新規劃主題':'產生學習範圍'}<Icon name="chevron-right" size={16}/></button>}</footer>
 </section>}
 {!inputStage&&draft&&view.status==='ready'&&<section className="surface topic-section topic-scope"><header className="tool-stage-heading"><div><h2>確認學習範圍</h2><p>AI 已依需求整理好學習範圍，可直接確認或微調。</p></div><button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>修改學習需求</button></header>
 <ScopeFields draft={draft} onChange={setDraft} busy={busy}/>
 {errorNotice}
 <footer className="tool-stage-footer"><span>確認後才會搜尋，來源由你選擇。</span><button className="primary-button" disabled={busy||!draft.title.trim()||!draft.level.trim()||!draft.topics.some(x=>x.trim())||!draft.goals.some(x=>x.trim())} onClick={()=>{const clean={...draft,goals:draft.goals.filter(x=>x.trim()),topics:draft.topics.filter(x=>x.trim()),exclude:draft.exclude.filter(x=>x.trim())};setDraft(clean);void perform(async()=>{const body=JSON.stringify(clean);if(approveIntent.current?.body!==body)approveIntent.current={body,key:crypto.randomUUID()};await api.studyTools(`/v1/topics/${view.topic_id}/approve`,{expected_version:view.version,proposal:clean},approveIntent.current.key);approveIntent.current=null;});}}>確認範圍並搜尋<Icon name="chevron-right" size={16}/></button></footer></section>}
 {!inputStage&&view?.status==='approved'&&<>

 {work&&<section className={`surface topic-section topic-sources${!hasMaterial?' is-selecting':''}`}>
 {hasMaterial?<header className="tool-stage-heading"><div><h2>{work.run_id?(completed?'教材與知識地圖已建立':cancelled?'已取消教材處理':failed?'教材處理失敗':cancelling?'正在取消教材處理':materialRun?.status==='pending'?'等待開始處理':materialRun?'正在分析教材':'正在讀取處理狀態'):work.deleted?'查詢已刪除':'正在建立教材'}</h2><p>{completed?(materialRun?.status==='partial'?'知識地圖已建立，可先查看已整理的內容；部分內容未完整整理。':`使用你選擇的 ${work.selection.length} 份來源，已完成這份教材。`):cancelling?'已收到取消要求，會在目前進行中的步驟完成後安全停止。':cancelled?'這次分析已停止。':failed?materialFailureMessage(materialRun.error_code??'MATERIAL_ANALYSIS_FAILED'):'取得的來源會保留，可離開頁面，稍後回來查看進度。'}</p></div>
 </header>:<header className="tool-stage-heading"><div><h2>選擇教材來源</h2><p>勾選想使用的論文或官方教學，再建立知識地圖。</p></div><span className="tool-status">{stages[work.status]}</span></header>}

 <div className="topic-approved-summary"><div><strong>{view.proposal?.title}</strong><span>{view.proposal?.level}</span></div><details><summary>已確認的學習範圍</summary><dl><dt>涵蓋主題</dt><dd>{view.proposal?.topics.join('；')}</dd><dt>學習目標</dt><dd>{view.proposal?.goals.join('；')}</dd><dt>不涵蓋</dt><dd>{view.proposal?.exclude.join('；')||'未另外指定'}</dd></dl></details><button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>重新規劃</button></div>
 {work.deleted&&<p className="tool-notice">查詢紀錄已刪除，已建立的教材與來源仍可查看。</p>}
 {work.error_code&&<p className="tool-notice">{sourceReasons[work.error_code]??'目前無法完成，已取得資料會保留。'}</p>}
 {work.status==='searching'&&<div className="tool-wait" role="status"><span className="loading-ring"/><p>正在搜尋論文與官方教學。</p></div>}
 {work.run_id&&<section className={`topic-analysis${analyzing?' is-processing':''}`} aria-label="教材分析子流程">
 {runError&&<p className="form-error" role="alert">無法讀取處理狀態：{runError}<button className="text-button" onClick={()=>setRunReload(v=>v+1)}>重新讀取進度</button></p>}
 {!materialRun&&!runError&&<p role="status">正在讀取教材分析進度…</p>}
 {materialRun&&<>
 {analyzing?<div className="processing-grid"><div className="topic-analysis-progress"><ProcessingProgress run={materialRun} now={now} showSources={false}/></div><ProcessingTimeline stages={materialProgressStages.slice(0,-1).map(stage=>({label:materialProgressStageLabel(stage),icon:stage==='semantics'?'map':'process'}))} currentIndex={materialProgressStages.indexOf(materialRun.progress_stage)} activity={cancelling?'取消中':'進行中'}/></div>:<div className="topic-analysis-result">
 <p role="status">{completed?(materialRun.status==='partial'?'部分結果可用':'處理完成'):`${failed?'最後記錄進度':'停止於'}：${materialProgressStageLabel(materialRun.progress_stage)}`}{materialRun.total_pages!==null&&` · 已處理 ${materialRun.completed_pages} / ${materialRun.total_pages} 頁`}</p>
 <p>已耗時：{materialElapsedLabel(materialRun.created_at,Date.parse(materialRun.completed_at??materialRun.updated_at))}</p>
 {failed&&materialRun.analysis_saved&&<p>已完成的處理進度已保存；可前往教材建立結果重試，接續未完成的部分。</p>}
 <ProcessingTimeline stages={materialProgressStages.slice(0,-1).map(stage=>({label:materialProgressStageLabel(stage),icon:stage==='semantics'?'map':'process'}))} currentIndex={completed?materialProgressStages.length-1:materialProgressStages.indexOf(materialRun.progress_stage)} activity={failed?'失敗':'已停止'}/>
 </div>}
 </>}
 </section>}
 {hasMaterial&&readOnlySources?<details className="topic-selected-sources"><summary>已選來源 · {work.selection.length} 份</summary><SourceCandidates candidates={visibleSources} selected={selected} disabled readOnly onChange={setSelected}/></details>:visibleSources.length>0&&<SourceCandidates candidates={visibleSources} selected={selected} disabled={busy||working||readOnlySources} readOnly={readOnlySources} onChange={setSelected}/>}

 {!work.candidates.length&&work.status==='selecting'&&<p className="tool-empty">沒有找到來源，請調整主題重新規劃。</p>}
 <footer className="tool-stage-footer"><div className="tool-footer-secondary">
 {!work.deleted&&<>
 {work.status==='selecting'&&work.cursor&&!hasMaterial&&<button className="text-button" disabled={busy} onClick={()=>void actResearch('more')}>載入更多論文</button>}
 {work.status==='failed'&&!work.run_id&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成部分</button>}
 {working&&work.status!=='ready'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('cancel')}>停止處理</button>}
 {work.status==='cancelled'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>接續處理</button>}
 {!hasMaterial&&<span>已選 {selected.length} 份來源</span>}
 </>}
 {binding&&work.run_id&&<button className="text-button" onClick={()=>writeRoute({name:'material-run',materialId:work.material_id!,runId:work.run_id!})}>查看教材建立結果</button>}
 </div>
 {!work.deleted&&work.status==='selecting'&&!hasMaterial&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/material`,{selected}))}>建立教材與知識地圖<Icon name="chevron-right" size={16}/></button>}
 {!work.deleted&&work.status==='failed'&&hasMaterial&&!work.run_id&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void actResearch('acquire',selected)}>使用選取來源繼續建立</button>}
 {binding&&materialRun?<button className="primary-button" onClick={()=>writeRoute({name:'knowledge-map',materialId:materialRun.material_id,runId:materialRun.run_id,structureRevision:binding.knowledge_structure_revision})}>開啟知識地圖<Icon name="chevron-right" size={16}/></button>:work.run_id&&<button className="primary-button" onClick={()=>writeRoute({name:'material-run',materialId:work.material_id!,runId:work.run_id!})}>查看教材建立結果<Icon name="chevron-right" size={16}/></button>}
 </footer></section>}
 </>}
 </section>;
}

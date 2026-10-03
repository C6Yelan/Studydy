import {useEffect,useRef,useState} from 'react';
import {errorMessage,type StudydyApiClient} from '../../api/client';
import {writeRoute} from '../../app/routes';
import { Icon } from '../../ui/Icon';
import { LearningSteps } from '../material-tools/LearningSteps';
import {SourceCandidates,sourceReasons} from '../material-tools/SourceCandidates';
import type {Research} from '../material-tools/ResearchPanel';
import '../material-tools/styles.css';
import './styles.css';

type Scope={title:string;level:string;goals:string[];topics:string[];exclude:string[]};
type Work=Research&{material_id:string|null;run?:{status:string;error_code:string|null;output_binding?:{knowledge_structure_revision:string}|null}};
type Topic={topic_id:string;request:string;proposal:Scope|null;version:number;status:string;research_id:string|null;research?:Work;error_code:string|null};
const active=new Set(['searching','acquiring','normalizing','ready']);
const stages:Record<string,string>={searching:'正在搜尋來源',selecting:'選擇想加入的來源',acquiring:'正在取得選取來源',normalizing:'正在轉換文件',ready:'正在建立教材分析',submitted:'已送入教材分析',failed:'處理未完成',cancelled:'已停止'};

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
 const completed=!!work?.run&&['partial','succeeded'].includes(work.run.status);
 const hasMaterial=!!work?.material_id;
 const readOnlySources=hasMaterial&&!(work?.status==='failed'&&!work.run_id);
 const visibleSources=work?.candidates.filter(c=>!readOnlySources||work.selection.includes(c.id))??[];
 const inputStage=editingRequest||!view||['pending','failed','cancelled'].includes(view.status);
 const step=inputStage?0:view.status==='ready'?1:completed?4:hasMaterial?3:2;
 return <section className="cards-page topic-page">
 <header className="cards-page-header"><div><h1>從主題建立教材</h1><p>告訴我們想學什麼，選擇資料後建立知識地圖。</p></div>{topicId&&<button className="secondary-button" onClick={()=>writeRoute({name:'topics'})}>新主題</button>}</header>
 <div className="topic-history-bar"><label className="tool-field">已保存的主題<select aria-label="已保存的主題" value={topicId??''} onChange={e=>writeRoute(e.target.value?{name:'topic',topicId:e.target.value}:{name:'topics'})}><option value="">新的學習主題</option>{list.map(t=><option key={t.topic_id} value={t.topic_id}>{t.proposal?.title??t.request}</option>)}</select></label></div>
 <LearningSteps labels={['學習主題','確認範圍','選取來源','建立教材']} current={step}/>
 {error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={()=>{setError('');setReload(v=>v+1);}}>重新讀取</button></p>}
 {inputStage&&<section className="surface topic-section topic-request"><div className="topic-request-heading"><span><Icon name="learning" size={28}/></span><h2>你想學什麼？</h2><p>可以是一個主題、一個問題，或想完成的學習目標。</p></div>
 <label className="tool-field"><span className="visually-hidden">想學的主題</span><textarea aria-label="想學的主題" disabled={busy||view?.status==='pending'||!!topicId&&!view} value={request} maxLength={1000} rows={4} onChange={e=>{requestLoaded.current=true;setRequest(e.target.value);}} placeholder="例如：我知道 IP 的用途，想了解 TCP 三次握手如何建立連線。"/></label>
 {view?.status==='pending'&&<div className="tool-wait" role="status"><span className="loading-ring"/><p>正在整理預計學習範圍；尚未搜尋外部來源。</p></div>}
 {view?.status==='failed'&&<p className="tool-notice">這次無法產生範圍，請重試。</p>}
 {view?.status==='cancelled'&&<p className="tool-notice">這個規劃已停止，可以建立新主題。</p>}
 <footer className="tool-stage-footer"><div className="tool-footer-secondary">
 {editingRequest&&<button className="secondary-button" disabled={busy} onClick={()=>setEditingRequest(false)}>返回目前規劃</button>}
 {view&&['pending','ready','failed'].includes(view.status)&&<button className="text-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'cancel',expected_version:view.version}))}>取消這個規劃</button>}
 </div>{view?.status==='failed'?<button className="primary-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'retry',expected_version:view.version}))}>重試規劃</button>:<button className="primary-button" disabled={busy||!request.trim()||view?.status==='pending'||!!topicId&&!view} onClick={()=>void generate()}>{view?'重新規劃主題':'產生學習範圍'}<Icon name="chevron-right" size={16}/></button>}</footer>
 </section>}
 {!inputStage&&draft&&view.status==='ready'&&<section className="surface topic-section topic-scope"><header className="tool-stage-heading"><div><h2>確認學習範圍</h2><p>調整程度、目標與主題，再開始搜尋資料。</p></div><button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>修改學習需求</button></header>
 <div className="topic-scope-grid"><label className="tool-field">教材名稱<input aria-label="教材名稱" disabled={busy} value={draft.title} maxLength={180} onChange={e=>setDraft({...draft,title:e.target.value})}/></label><label className="tool-field">學習程度<input aria-label="學習程度" disabled={busy} value={draft.level} maxLength={300} onChange={e=>setDraft({...draft,level:e.target.value})}/></label>
 {(['goals','topics'] as const).map((field,i)=><label className="tool-field" key={field}>{['學習目標','要涵蓋的主題'][i]}<textarea aria-label={['學習目標','要涵蓋的主題'][i]} rows={5} disabled={busy} value={draft[field].join('\n')} onChange={e=>setDraft({...draft,[field]:e.target.value.split('\n')})}/><small>每行一項</small></label>)}</div>
 <details className="topic-exclude"><summary>本次不涵蓋的內容{draft.exclude.length>0?` · ${draft.exclude.length} 項`:''}</summary><label className="tool-field">本次不涵蓋<textarea aria-label="本次不涵蓋" rows={3} disabled={busy} value={draft.exclude.join('\n')} onChange={e=>setDraft({...draft,exclude:e.target.value.split('\n')})}/><small>每行一項；可以留白。</small></label></details>
 <footer className="tool-stage-footer"><span>確認後才會搜尋，來源由你選擇。</span><button className="primary-button" disabled={busy||!draft.title.trim()||!draft.level.trim()||!draft.topics.some(x=>x.trim())||!draft.goals.some(x=>x.trim())} onClick={()=>{const clean={...draft,goals:draft.goals.filter(x=>x.trim()),topics:draft.topics.filter(x=>x.trim()),exclude:draft.exclude.filter(x=>x.trim())};setDraft(clean);void perform(async()=>{const body=JSON.stringify(clean);if(approveIntent.current?.body!==body)approveIntent.current={body,key:crypto.randomUUID()};await api.studyTools(`/v1/topics/${view.topic_id}/approve`,{expected_version:view.version,proposal:clean},approveIntent.current.key);approveIntent.current=null;});}}>確認範圍並搜尋<Icon name="chevron-right" size={16}/></button></footer></section>}
 {!inputStage&&view?.status==='approved'&&<>
 <div className="topic-approved-summary"><div><strong>{view.proposal?.title}</strong><span>{view.proposal?.level}</span></div><details><summary>已確認的學習範圍</summary><dl><dt>涵蓋主題</dt><dd>{view.proposal?.topics.join('；')}</dd><dt>學習目標</dt><dd>{view.proposal?.goals.join('；')}</dd><dt>不涵蓋</dt><dd>{view.proposal?.exclude.join('；')||'未另外指定'}</dd></dl></details><button className="text-button" disabled={busy} onClick={()=>setEditingRequest(true)}>重新規劃</button></div>
 {work&&<section className={`surface topic-section topic-sources${!hasMaterial?' is-selecting':''}`}>
 {hasMaterial?<div className={`tool-result${completed?' is-complete':''}`}><span className="tool-result-icon"><Icon name={completed?'check':'process'} size={26}/></span><div><h2>{completed?'教材與知識地圖已建立':work.run?.status==='failed'?'教材分析尚未完成':work.run?.status==='cancelled'?'教材分析已停止':'正在建立教材'}</h2><p>{completed?`使用你選擇的 ${work.selection.length} 份來源，已完成這份教材。`:'取得的來源會保留，可離開頁面，稍後回來查看進度。'}</p></div>
 {work.run?.output_binding&&work.run_id?<button className="primary-button" onClick={()=>writeRoute({name:'knowledge-map',materialId:work.material_id!,runId:work.run_id!,structureRevision:work.run!.output_binding!.knowledge_structure_revision})}>開啟知識地圖<Icon name="chevron-right" size={16}/></button>:work.run_id&&<button className="primary-button" onClick={()=>writeRoute({name:'material-run',materialId:work.material_id!,runId:work.run_id!})}>查看教材建立結果<Icon name="chevron-right" size={16}/></button>}
 </div>:<header className="tool-stage-heading"><div><h2>選擇教材來源</h2><p>勾選想使用的論文或官方教學，再建立知識地圖。</p></div><span className="tool-status">{stages[work.status]}</span></header>}
 {work.error_code&&<p className="tool-notice">{sourceReasons[work.error_code]??'目前無法完成，已取得資料會保留。'}</p>}
 {work.status==='searching'&&<div className="tool-wait" role="status"><span className="loading-ring"/><p>正在搜尋論文與官方教學。</p></div>}
 {hasMaterial&&<h3 className="tool-section-label">{completed?'已使用的來源':'選取的來源'} · {work.selection.length} 份</h3>}
 {visibleSources.length>0&&<SourceCandidates candidates={visibleSources} selected={selected} disabled={busy||working||readOnlySources} readOnly={readOnlySources} onChange={setSelected}/>}
 {!work.candidates.length&&work.status==='selecting'&&<p className="tool-empty">沒有找到來源，請調整主題重新規劃。</p>}
 <footer className="tool-stage-footer"><div className="tool-footer-secondary">
 {work.status==='selecting'&&work.cursor&&!hasMaterial&&<button className="text-button" disabled={busy} onClick={()=>void actResearch('more')}>載入更多論文</button>}
 {work.status==='failed'&&!work.run_id&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成部分</button>}
 {working&&work.status!=='ready'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('cancel')}>停止處理</button>}
 {work.status==='cancelled'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>接續處理</button>}
 {!hasMaterial&&<span>已選 {selected.length} 份來源</span>}
 {work.run?.output_binding&&work.run_id&&<button className="text-button" onClick={()=>writeRoute({name:'material-run',materialId:work.material_id!,runId:work.run_id!})}>查看教材建立結果</button>}
 </div>
 {work.status==='selecting'&&!hasMaterial&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/material`,{selected}))}>建立教材與知識地圖<Icon name="chevron-right" size={16}/></button>}
 {work.status==='failed'&&hasMaterial&&!work.run_id&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void actResearch('acquire',selected)}>使用選取來源繼續建立</button>}
 </footer></section>}
 </>}
 </section>;
}

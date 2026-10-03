import {useEffect,useRef,useState} from 'react';
import {errorMessage,type StudydyApiClient} from '../../api/client';
import {writeRoute} from '../../app/routes';
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
 const requestLoaded=useRef(false);
 const alive=useRef(true),loadedVersion=useRef(''),selectionLoaded=useRef(''),createIntent=useRef<{body:string;key:string}|null>(null),approveIntent=useRef<{body:string;key:string}|null>(null);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 useEffect(()=>{let stop=false;void api.studyTools<{topics:Topic[]}>('/v1/topics').then(v=>{if(!stop)setList(v.topics);},e=>{if(!stop)setError(errorMessage(e));});return()=>{stop=true;};},[api,reload]);
 useEffect(()=>{if(!topicId)return;let stop=false;let timer:ReturnType<typeof setTimeout>;
 const read=async()=>{try{const v=await api.studyTools<Topic>(`/v1/topics/${topicId}`);if(stop)return;setView(v);if(v.proposal&&loadedVersion.current!==`${v.topic_id}/${v.version}`){setDraft(v.proposal);loadedVersion.current=`${v.topic_id}/${v.version}`;}if(!requestLoaded.current){setRequest(v.request);requestLoaded.current=true;}
 if(v.research&&selectionLoaded.current!==v.research.research_id){setSelected(v.research.selection);selectionLoaded.current=v.research.research_id;}
 if(v.status==='pending'||v.research&&(active.has(v.research.status)||v.research.run&&['pending','running'].includes(v.research.run.status)))timer=setTimeout(read,1800);
 }catch(e){if(!stop)setError(errorMessage(e));}};void read();return()=>{stop=true;clearTimeout(timer);};},[api,topicId,reload]);
 const perform=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();if(alive.current)setReload(v=>v+1);}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 const generate=()=>perform(async()=>{const body=JSON.stringify({request});if(createIntent.current?.body!==body)createIntent.current={body,key:crypto.randomUUID()};const t=await api.studyTools<Topic>('/v1/topics',{request},createIntent.current.key);createIntent.current=null;if(alive.current)writeRoute({name:'topic',topicId:t.topic_id});});
 const actResearch=(action:string,ids?:string[])=>perform(()=>api.studyTools(`/v1/research/${view?.research_id}/actions`,{action,...(ids?{selected:ids}:{})}));
 const work=view?.research,working=!!work&&active.has(work.status);
 return <section className="cards-page topic-page"><header className="cards-page-header"><div><h1>從主題建立教材</h1><p>先確認想學的範圍，再挑選來源建立知識地圖。</p></div><button className="secondary-button" onClick={()=>writeRoute({name:'topics'})}>新主題</button></header>
 <label className="topic-history">已保存的主題<select aria-label="已保存的主題" value={topicId??''} onChange={e=>writeRoute(e.target.value?{name:'topic',topicId:e.target.value}:{name:'topics'})}><option value="">新的學習主題</option>{list.map(t=><option key={t.topic_id} value={t.topic_id}>{t.proposal?.title??t.request}</option>)}</select></label>
 {error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={()=>{setError('');setReload(v=>v+1);}}>重新讀取</button></p>}
 <section className="surface topic-section"><h2>1. 想學什麼？</h2><textarea aria-label="想學的主題" disabled={busy||view?.status==='pending'||!!topicId&&!view} value={request} maxLength={1000} rows={3} onChange={e=>{requestLoaded.current=true;setRequest(e.target.value);}} placeholder="例如：我想從零開始了解 TCP 三次握手"/><button className="primary-button" disabled={busy||!request.trim()||view?.status==='pending'} onClick={()=>void generate()}>{view?'重新規劃主題':'產生學習範圍'}</button>
 {view?.status==='pending'&&<p role="status">正在整理預計學習範圍；尚未搜尋外部來源。</p>}
 {view?.status==='failed'&&<><p>這次無法產生範圍，請重試。</p><button className="secondary-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'retry',expected_version:view.version}))}>重試規劃</button></>}
 {view&&['pending','ready','failed'].includes(view.status)&&<button className="text-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/actions`,{action:'cancel',expected_version:view.version}))}>取消這個規劃</button>}
 </section>
 {view?.status==='cancelled'&&<p>這個規劃已停止，可以建立新主題。</p>}
 {draft&&view?.status==='ready'&&<section className="surface topic-section"><h2>2. 確認學習範圍</h2><p>這是可修改的學習規劃，確認後才會開始搜尋。</p><div className="topic-scope-grid"><label>教材名稱<input aria-label="教材名稱" disabled={busy} value={draft.title} maxLength={180} onChange={e=>setDraft({...draft,title:e.target.value})}/></label><label>學習程度<input aria-label="學習程度" disabled={busy} value={draft.level} maxLength={300} onChange={e=>setDraft({...draft,level:e.target.value})}/></label>
 {(['goals','topics','exclude'] as const).map((field,i)=><label key={field}>{['學習目標','要涵蓋的主題','本次不涵蓋'][i]}<textarea aria-label={['學習目標','要涵蓋的主題','本次不涵蓋'][i]} rows={5} disabled={busy} value={draft[field].join('\n')} onChange={e=>setDraft({...draft,[field]:e.target.value.split('\n')})}/><small>每行一項</small></label>)}</div>
 <button className="primary-button" disabled={busy||!draft.title.trim()||!draft.level.trim()||!draft.topics.some(x=>x.trim())||!draft.goals.some(x=>x.trim())} onClick={()=>{const clean={...draft,goals:draft.goals.filter(x=>x.trim()),topics:draft.topics.filter(x=>x.trim()),exclude:draft.exclude.filter(x=>x.trim())};setDraft(clean);void perform(async()=>{const body=JSON.stringify(clean);if(approveIntent.current?.body!==body)approveIntent.current={body,key:crypto.randomUUID()};await api.studyTools(`/v1/topics/${view.topic_id}/approve`,{expected_version:view.version,proposal:clean},approveIntent.current.key);approveIntent.current=null;});}}>確認範圍並搜尋</button></section>}
 {view?.status==='approved'&&view.proposal&&<section className="surface topic-section"><h2>已確認的學習範圍</h2><strong>{view.proposal.title}</strong><p>{view.proposal.level}</p><ul>{view.proposal.topics.map((t,i)=><li key={i}>{t}</li>)}</ul><details><summary>學習目標與排除範圍</summary><p>{view.proposal.goals.join('；')}</p><p>{view.proposal.exclude.join('；')||'未另外指定排除項目'}</p></details></section>}
 {work&&<section className="surface topic-section research-panel"><h2>3. 選來源，建立教材</h2><p role="status">{stages[work.status]}</p><p>知識地圖的內容範圍，以你選取的來源為準。</p>{work.error_code&&<p>{sourceReasons[work.error_code]??'目前無法完成，已取得資料會保留。'}</p>}
 <SourceCandidates candidates={work.candidates} selected={selected} disabled={busy||working||!!work.material_id&&!(work.status==='failed'&&!work.run_id)} onChange={setSelected}/>
 {!work.candidates.length&&work.status==='selecting'&&<p>沒有找到來源，請調整主題重新規劃。</p>}
 <div className="state-actions">{work.status==='selecting'&&work.cursor&&!work.material_id&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('more')}>載入更多論文</button>}
 {work.status==='selecting'&&!work.material_id&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void perform(()=>api.studyTools(`/v1/topics/${view.topic_id}/material`,{selected}))}>建立教材與知識地圖</button>}
 {work.status==='failed'&&work.material_id&&!work.run_id&&<button className="primary-button" disabled={busy||!selected.length} onClick={()=>void actResearch('acquire',selected)}>使用選取來源繼續建立</button>}
 {work.status==='failed'&&!work.run_id&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>重試未完成部分</button>}
 {working&&work.status!=='ready'&&<button className="text-button" disabled={busy} onClick={()=>void actResearch('cancel')}>停止處理</button>}
 {work.status==='cancelled'&&<button className="secondary-button" disabled={busy} onClick={()=>void actResearch('retry')}>接續處理</button>}
 {work.run_id&&work.material_id&&<button className="primary-button" onClick={()=>writeRoute({name:'material-run',materialId:work.material_id!,runId:work.run_id!})}>查看教材建立結果</button>}
 {work.run?.output_binding&&work.material_id&&work.run_id&&<button className="secondary-button" onClick={()=>writeRoute({name:'knowledge-map',materialId:work.material_id!,runId:work.run_id!,structureRevision:work.run!.output_binding!.knowledge_structure_revision})}>開啟知識地圖</button>}
 </div>{work.material_id&&!work.run_id&&<p>教材正在建立，可離開頁面，稍後由這個主題繼續查看。</p>}
 {work.run&&<p>{['partial','succeeded'].includes(work.run.status)?'知識地圖已建立，來源與品質提示一併保留。':work.run.status==='failed'?'分析尚未完成，可到建立結果頁接續已保存進度。':work.run.status==='cancelled'?'教材分析已停止，已取得的來源仍保留。':'教材分析進行中。'}</p>}
 </section>}
 </section>;
}

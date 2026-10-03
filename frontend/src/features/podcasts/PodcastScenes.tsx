import {useEffect,useState,type RefObject} from 'react';
import {errorMessage,type StudydyApiClient} from '../../api/client';
import type {EvidenceView,PodcastView} from '../../api/contracts';
import {SourceButton,sourceLinks} from '../../ui/SourceButton';
import {formatTime} from './PodcastPlayer';
import {Icon} from '../../ui/Icon';
import './scenes.css';

type Scene={index:number;start:number;end:number;claim_id:string;kind:'concept'|'comparison'|'flow';title:string;text:string;evidence:EvidenceView[];steps:string[];columns:{label:string;claims:{claim_id?:string;text:string;evidence:EvidenceView[]}[]}[]};
type Manifest={audio_sha256:string;duration:number;source_resolver:string;scenes:Scene[]};
type Episode={index:number;status:string;version:number;error_code:string|null;manifest:Manifest|null};
type Scenes={podcast_id:string;episodes:Episode[]};
const reasons:Record<string,string>={SCENE_ALIGNMENT_FAILED:'這集尚無法可靠對齊語音與講稿，可重試。',SCENE_ALIGNMENT_INVALID:'對齊資料不完整，尚未顯示同步畫面。',SCENE_SOURCE_CHANGED:'音訊或講稿已變更，請重新準備同步畫面。',SCENE_SOURCE_CHECK_INVALID:'來源核對尚未完成，可重試。',SCENE_PREPARATION_FAILED:'同步畫面暫時無法完成，可重試。'};

export function PodcastScenes({api,view,index,media,active}:{api:StudydyApiClient;view:PodcastView;index:number;media:RefObject<HTMLAudioElement|null>;active:boolean}){
 const [data,setData]=useState<Scenes|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0),[clock,setClock]=useState({index,time:0}),[playing,setPlaying]=useState(false);
 const time=clock.index===index?clock.time:0;
 useEffect(()=>{if(!active)return;let cancelled=false;let timer:ReturnType<typeof setTimeout>;setData(previous=>previous?.podcast_id===view.podcast_id?previous:null);setError('');
 const read=async()=>{try{const result=await api.studyTools<Scenes>(`/v1/podcasts/${view.podcast_id}/scenes`);if(cancelled)return;if(result.podcast_id!==view.podcast_id)throw new Error('同步畫面來源不符。');setData(result);if(result.episodes.some(e=>['pending','running'].includes(e.status)))timer=setTimeout(read,1800);}catch(e){if(!cancelled)setError(errorMessage(e));}};
 void read();return()=>{cancelled=true;clearTimeout(timer);};},[api,view.podcast_id,reload,active]);
 useEffect(()=>{if(!active)return;const element=media.current;if(!element)return;let frame=0;
 const update=()=>{setClock({index,time:element.currentTime});setPlaying(!element.paused&&!element.ended&&!element.error);if(!element.paused&&!element.ended&&!element.error&&!document.hidden){if(!frame)frame=requestAnimationFrame(()=>{frame=0;update();});}else{cancelAnimationFrame(frame);frame=0;}};
 const events=['loadedmetadata','timeupdate','seeking','seeked','play','pause','ratechange','ended','waiting','error'];events.forEach(name=>element.addEventListener(name,update));document.addEventListener('visibilitychange',update);update();
 return()=>{cancelAnimationFrame(frame);events.forEach(name=>element.removeEventListener(name,update));document.removeEventListener('visibilitychange',update);};},[active,index,media]);
 const perform=async(fn:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await fn();setReload(n=>n+1);}catch(e){setError(errorMessage(e));}finally{setBusy(false);}};
 const episode=data?.episodes.find(e=>e.index===index),manifest=episode?.manifest;
 const matches=manifest&&manifest.audio_sha256===view.episodes[index].audio?.sha256;
 const scene=matches?manifest.scenes.find(s=>time>=s.start&&time<s.end)??(time>=manifest.duration?manifest.scenes.at(-1):manifest.scenes[0]):null;
 const done=data?.episodes.filter(e=>e.status==='ready').length??0;
 const jump=(target:number)=>{if(media.current&&manifest&&manifest.scenes[target]){media.current.currentTime=manifest.scenes[target].start;setClock({index,time:manifest.scenes[target].start});}};
 const references=scene?sourceLinks([...scene.evidence,...scene.columns.flatMap(c=>c.claims.flatMap(q=>q.evidence))]):[];
 return <div className="podcast-scenes">
 <div className="scene-toolbar"><p>{done===view.episode_count?'跟隨音訊同步顯示教材重點':`同步畫面準備中 · ${done} / ${view.episode_count} 集`}</p>{data?.episodes.some(e=>e.status==='unprepared')&&<button className="primary-button" disabled={busy||view.status!=='ready'} onClick={()=>void perform(()=>api.studyTools(`/v1/podcasts/${view.podcast_id}/scenes`,{}))}>準備整份同步畫面</button>}</div>
 {error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={()=>setReload(n=>n+1)}>重新讀取</button></p>}
 {!data&&!error&&<p>正在讀取同步畫面。</p>}
 {episode?.status==='unprepared'&&<p>使用這份講稿與既有音訊，準備概念圖卡、來源中的比較及明確流程。</p>}
 {episode&&['pending','running'].includes(episode.status)&&<p role="status">{episode.status==='running'?'正在對齊本集語音與講稿':'本集正在等待準備'}。音訊仍可播放。<button className="text-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/podcasts/${view.podcast_id}/scenes/${index}/actions`,{action:'cancel',expected_version:episode.version}))}>取消本集準備</button></p>}
 {episode&&['failed','cancelled'].includes(episode.status)&&<p role="status">{episode.status==='cancelled'?'本集同步畫面已停止準備。':reasons[episode.error_code??'']??'本集同步畫面尚未完成。'}<button className="secondary-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/podcasts/${view.podcast_id}/scenes/${index}/actions`,{action:'retry',expected_version:episode.version}))}>重試本集同步畫面</button></p>}
 {episode?.status==='ready'&&!matches&&<p role="alert">同步資料與目前音訊不一致，請重新讀取。</p>}
 {scene&&manifest&&<><article className={`synced-scene scene-kind-${scene.kind}`} data-scene-index={scene.index} data-scene-kind={scene.kind} data-media-time={time.toFixed(3)} data-scene-start={scene.start} data-scene-end={scene.end}>
 <header><span>{scene.kind==='flow'?'流程':scene.kind==='comparison'?'概念比較':'概念重點'}</span><small>{scene.index+1} / {manifest.scenes.length} 段 · {playing?'播放中':'已暫停'}</small></header><h3>{scene.kind==='comparison'?scene.columns.map(c=>c.label).join('與'):scene.title}</h3>
 {scene.kind==='flow'?<ol className="scene-flow">{scene.steps.map((step,i)=><li key={i}><span>{i+1}</span><p>{step}</p></li>)}</ol>:scene.kind==='comparison'?<div className="scene-comparison">{scene.columns.map(c=><section key={c.label} className={c.claims.some(q=>q.claim_id===scene.claim_id)?'is-current':''}><h4>{c.label}</h4><ul>{c.claims.map((q,i)=><li key={i} className={q.claim_id===scene.claim_id?'is-current':''}>{q.text}</li>)}</ul></section>)}</div>:<p className="scene-main-text">{scene.text}</p>}
 {scene.kind!=='concept'&&<p className="scene-current-claim">本段重點：{scene.text}</p>}
 <div className="scene-progress" aria-hidden="true"><span style={{width:`${100*Math.max(0,Math.min(1,(time-scene.start)/(scene.end-scene.start)))}%`}}/></div>
 </article>
 <div className="scene-navigation" aria-label="本集同步段落"><button className="scene-skip" aria-label="上一段" disabled={!media.current||media.current.readyState===0||scene.index===0} onClick={()=>jump(scene.index-1)}><Icon name="arrow-left" size={17}/></button>
 <label><span className="scene-navigation-label">目前段落</span><select aria-label="跳至同步段落" value={scene.index} disabled={!media.current||media.current.readyState===0} onChange={e=>jump(Number(e.target.value))}>{manifest.scenes.map(s=><option key={s.index} value={s.index}>{s.index+1}. {s.title} · {formatTime(s.start)}</option>)}</select></label>
 <button className="scene-skip" aria-label="下一段" disabled={!media.current||media.current.readyState===0||scene.index===manifest.scenes.length-1} onClick={()=>jump(scene.index+1)}><Icon name="chevron-right" size={17}/></button></div>
 <details className="scene-sources" onToggle={e=>{if(e.currentTarget.open)media.current?.pause();}}><summary>查看這段來源 · {references.length} 頁</summary>{references.map(e=><SourceButton key={e.evidence_id} apiClient={api} resolver={manifest.source_resolver} evidence={e}/>)}</details></>}
 </div>;
}

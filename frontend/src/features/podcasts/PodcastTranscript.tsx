import { useEffect, useRef, useState, type RefObject } from 'react';
import { ApiClientError, errorMessage, type StudydyApiClient } from '../../api/client';
import type { EvidenceView, PodcastContext, PodcastView } from '../../api/contracts';
import { formatTime } from './PodcastPlayer';

export type Timeline={schema:'podcast-transcript-timeline/v1';podcast_id:string;episode_index:number;audio_sha256:string;script_sha256:string;source_resolver:string;
  segments:{id:string;start:number;end:number;text:string;title:string;evidence:EvidenceView[];source_refs?:PodcastContext['source_refs'];turns:{speaker:'host'|'guest';text:string}[]}[]};

function validTimeline(value:Timeline,view:PodcastView,index:number):boolean {
  const episode=view.episodes[index];
  if(value.schema!=='podcast-transcript-timeline/v1'||value.podcast_id!==view.podcast_id||value.episode_index!==index
    ||value.audio_sha256!==episode.audio?.sha256||(episode.script_sha256&&value.script_sha256!==episode.script_sha256)
    ||value.source_resolver!==view.source_resolver||!Array.isArray(value.segments)||!value.segments.length)return false;
  const evidence=episode.claims.flatMap(c=>c.evidence);let previous=0;
  try {
    for(const cue of value.segments) {
      if(!cue || typeof cue.id!=='string'||typeof cue.title!=='string'||typeof cue.text!=='string'
        ||!Number.isFinite(cue.start)||!Number.isFinite(cue.end)||cue.start!==previous||cue.end<=cue.start
        ||cue.end>(episode.audio?.duration_seconds??0)||!Array.isArray(cue.turns)||!cue.turns.length)return false;
      if(cue.turns.some(t=>!['host','guest'].includes(t.speaker)||typeof t.text!=='string')
        ||cue.text!==cue.turns.map(t=>t.text).join('\n'))return false;
      if(cue.evidence!==undefined && (!Array.isArray(cue.evidence)||cue.evidence.some(e=>!evidence.some(original=>
        original.evidence_id===e.evidence_id&&original.page_ref===e.page_ref&&original.page===e.page&&original.block_order===e.block_order&&original.quote===e.quote))))return false;
      if(episode.script?.schema==='podcast-script/v2'&&!cue.source_refs)return false;
      if(cue.source_refs) {
        if(cue.source_refs.length!==cue.turns.length)return false;
        for(const [i,ref] of cue.source_refs.entries()) {
          if(![ref.segment_index,ref.turn_index,ref.start,ref.end].every(Number.isInteger)||ref.segment_index<0||ref.turn_index<0||ref.start<0||ref.end<=ref.start)return false;
          const original=episode.script?.segments[ref.segment_index]?.turns[ref.turn_index];
          if(!original||ref.end>Array.from(original.text).length||original.speaker!==cue.turns[i].speaker
            ||Array.from(original.text).slice(ref.start,ref.end).join('')!==cue.turns[i].text)return false;
        }
      }
      previous=cue.end;
    }
    return previous===episode.audio?.duration_seconds;
  }catch{return false}
}

export function PodcastTranscript({api,view,index,media,active,videoVersion}:{
  api:StudydyApiClient;view:PodcastView;index:number;media:RefObject<HTMLMediaElement|null>;active:boolean;videoVersion:number;
}){
  const episode=view.episodes[index];
  const [timeline,setTimeline]=useState<Timeline|null>(null),[error,setError]=useState('');
  const [clock,setClock]=useState({index:-1,ready:false,playing:false});
  const list=useRef<HTMLOListElement>(null);
  useEffect(()=>{
    let cancelled=false;let timer:ReturnType<typeof setTimeout>|undefined;setTimeline(null);setError('');
    if(!episode.audio)return;
    const read=()=>void api.studyTools<Timeline>(`/v1/podcasts/${view.podcast_id}/episodes/${index}/timeline`).then(value=>{
      if(cancelled)return;
      if(!validTimeline(value,view,index)){setError('時間軸與本集原稿、音訊或來源不一致。');return}
      setTimeline(value);
    },e=>{if(cancelled)return;if(e instanceof ApiClientError&&e.status===409)timer=setTimeout(read,2000);else setError(errorMessage(e))});
    read();return()=>{cancelled=true;clearTimeout(timer)};
  },[api,view.podcast_id,index,episode.audio?.sha256,episode.script_sha256,view.source_resolver,videoVersion]);
  useEffect(() => { const panel = list.current?.closest<HTMLElement>(".podcast-transcript"); if (panel) panel.scrollTop = 0; }, [index]);
  const current=timeline?.podcast_id===view.podcast_id&&timeline.episode_index===index?timeline:null;
  useEffect(()=>{
    if(!active||!current)return;
    let raf=0;
    const tick=()=>{
      const element=media.current;
      const next={index:current.segments.findIndex(s=>s.start<=(element?.currentTime??0)&&(element?.currentTime??0)<s.end),ready:!!element&&element.readyState>0,playing:!!element&&!element.paused};
      setClock(p=>p.index===next.index&&p.ready===next.ready&&p.playing===next.playing?p:next);
      raf=requestAnimationFrame(tick);
    };
    tick();return()=>cancelAnimationFrame(raf);
  },[active,current,media]);
  useEffect(()=>{
    if(!clock.playing)return;
    const item=list.current?.querySelector<HTMLElement>('button[aria-current=true]'),panel=list.current?.closest<HTMLElement>('.podcast-transcript');
    if(!item||!panel||panel.scrollHeight<=panel.clientHeight||item.contains(document.activeElement))return;
    const a=item.getBoundingClientRect(),b=panel.getBoundingClientRect();
    if(a.top<b.top||a.bottom>b.bottom)panel.scrollTop+=a.top-b.top;
  },[clock.index,clock.playing]);
  if(!episode.script)return <p className="podcast-meta-note">本集逐字稿尚未生成。</p>;
  return <>
    {error&&<p className="form-error" role="alert">{error}</p>}
    {current?<><p className="podcast-meta-note">點擊講稿可跳到對應位置。</p><ol ref={list} className="podcast-timed-transcript">{current.segments.map((s,i)=><li key={s.id}>
      <button type="button" aria-current={clock.index===i?'true':undefined} disabled={!clock.ready} onClick={()=>{if(media.current)media.current.currentTime=s.start}}>
        <time>{formatTime(s.start)}</time><span>{s.turns.map((turn,j)=><span className="transcript-turn" key={j}>
          <small>{episode.delivery==='solo'?'旁白':turn.speaker==='host'?'學習者':'講解者'}</small><span>{turn.text}</span>
        </span>)}</span>
      </button>
    </li>)}</ol></>
      :<><p className="podcast-meta-note">時間軸尚未準備完成，可先閱讀逐字稿。</p><ol ref={list}>{episode.script.segments.map((s,i)=><li key={s.beat_id??i}>
        {s.title&&<strong>{s.title}</strong>}{s.turns.map((turn,j)=><div key={j}><span className="podcast-transcript-speaker">{episode.delivery==='solo'?'旁白':turn.speaker==='host'?'學習者':'講解者'}</span><p>{turn.text}</p></div>)}
      </li>)}</ol></>}
  </>;
}

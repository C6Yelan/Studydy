import { useEffect, useRef, useState, type RefObject } from 'react';
import { ApiClientError, errorMessage, type StudydyApiClient } from '../../api/client';
import type { PodcastView } from '../../api/contracts';
import { formatTime } from './PodcastPlayer';

type Timeline={schema:'podcast-transcript-timeline/v1';podcast_id:string;episode_index:number;audio_sha256:string;
  segments:{id:string;start:number;end:number;text:string;title:string;turns:{speaker:'host'|'guest';text:string}[]}[]};

export function PodcastTranscript({api,view,index,media,active,videoVersion}:{
  api:StudydyApiClient;view:PodcastView;index:number;media:RefObject<HTMLMediaElement|null>;active:boolean;videoVersion:number;
}){
  const episode=view.episodes[index];
  const [timeline,setTimeline]=useState<Timeline|null>(null),[error,setError]=useState('');
  const [clock,setClock]=useState({index:-1,ready:false,playing:false});
  const list=useRef<HTMLOListElement>(null);
  useEffect(()=>{
    let cancelled=false;setTimeline(null);setError('');
    if(!episode.audio||!active)return;
    void api.studyTools<Timeline>(`/v1/podcasts/${view.podcast_id}/episodes/${index}/timeline`).then(value=>{
      if(cancelled)return;
      if(value.schema!=='podcast-transcript-timeline/v1'||value.podcast_id!==view.podcast_id||value.episode_index!==index||value.audio_sha256!==episode.audio?.sha256){setError('時間軸與本集音訊不一致。');return}
      setTimeline(value);
    },e=>{if(!cancelled&&!(e instanceof ApiClientError&&e.status===409))setError(errorMessage(e))});
    return()=>{cancelled=true};
  },[api,view.podcast_id,index,episode.audio?.sha256,active,videoVersion]);
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
    const item=list.current?.querySelector<HTMLElement>('button[aria-current=true]'),panel=list.current?.closest<HTMLElement>('[role=tabpanel]');
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
    </li>)}</ol><p className="podcast-timeline-downloads"><a href={`/v1/podcasts/${view.podcast_id}/episodes/${index}/timeline`} download={`podcast-${index+1}-timeline.json`}>下載時間軸</a><a href={`/v1/podcasts/${view.podcast_id}/episodes/${index}/subtitles`} download={`podcast-${index+1}.vtt`}>下載字幕</a></p></>
      :<ol>{episode.script.segments.flatMap((s,i)=>s.turns.map((turn,j)=><li key={`${i}/${j}`}><span className="podcast-transcript-speaker">{episode.delivery==='solo'?'旁白':turn.speaker==='host'?'學習者':'講解者'}</span><p>{turn.text}</p></li>))}</ol>}
  </>;
}

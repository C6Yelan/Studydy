import { useEffect, useRef, useState } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';

export type VideoView = {
  schema: 'podcast-video/v1'; podcast_id: string; episode_index: number;
  status: 'waiting_audio' | 'unprepared' | 'pending' | 'running' | 'ready' | 'failed' | 'cancelled';
  version: number; error_code: string | null;
  video: null | { audio_sha256: string; script_sha256: string; artifact_id: string; sha256: string;
    width: number; height: number; fps: number; duration: number; pages: { title: string; start: number; end: number }[] };
};

export function usePodcastVideo(api: StudydyApiClient, podcastId: string, index: number, audioSha?: string, scriptSha?: string) {
  const [state,setState]=useState<VideoView|null>(null),[error,setError]=useState(''),[reload,setReload]=useState(0);
  const [busy,setBusy]=useState(false),alive=useRef(false);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
  const path=`/v1/podcasts/${podcastId}/episodes/${index}/video`;
  useEffect(()=>{
    let cancelled=false,timer:ReturnType<typeof setTimeout>|undefined;
    setState(null);setError('');
    if(!audioSha)return;
    const read=async()=>{
      try {
        const value=await api.studyTools<VideoView>(path);
        if(value.schema!=='podcast-video/v1'||value.podcast_id!==podcastId||value.episode_index!==index
          ||(value.status==='ready'&&(!value.video||value.video.audio_sha256!==audioSha||(scriptSha&&value.video.script_sha256!==scriptSha))))throw Error('影片與本集來源不一致。');
        if(cancelled)return;
        setState(value);setError('');
        if(['pending','running'].includes(value.status))timer=setTimeout(read,2000);
      }catch(e){if(!cancelled)setError(errorMessage(e));}
    };
    void read();return()=>{cancelled=true;clearTimeout(timer)};
  },[api,path,podcastId,index,audioSha,scriptSha,reload]);
  const current=state?.podcast_id===podcastId&&state.episode_index===index?state:null;
  const act=async(action:'prepare'|'retry'|'cancel')=>{
    if(busy)return;setBusy(true);setError('');
    try{
      await api.studyTools(action==='prepare'?path:path+'/actions',action==='prepare'?{}:{action,expected_version:current?.version});
      if(alive.current)setReload(n=>n+1);
    }catch(e){if(alive.current)setError(errorMessage(e));}
    finally{if(alive.current)setBusy(false);}
  };
  return {state:current,error,busy,act,reload:()=>setReload(n=>n+1)};
}

const reasons:Record<string,string>={
  VIDEO_AUDIO_INVALID:'影片音訊未通過響度或峰值核對，尚未發布。',
  VIDEO_DISK_SPACE_LOW:'儲存空間不足，已停止影片工作。',VIDEO_LAYOUT_INVALID:'分鏡版面需要調整，尚未發布影片。',
  VIDEO_STORYBOARD_NEEDS_REVIEW:'分鏡來源或教學品質核對未通過，尚未發布影片。',VIDEO_TRANSCRIPT_INVALID:'講稿切分未通過核對。',
  SCENE_ALIGNMENT_FAILED:'暫時無法可靠對齊講稿與語音。',VIDEO_ALIGNMENT_INVALID:'語音時間核對未通過。',
  VIDEO_SOURCE_CHANGED:'原稿或音訊版本已變更，無法使用這份影片。',VIDEO_INTERRUPTED:'影片工作中斷，可重試。',
  VIDEO_TOO_LARGE:'影片超過儲存限制。',VIDEO_PROVIDER_UNAVAILABLE:'影片服務目前無法使用。',
  VIDEO_RENDER_FAILED:'影片繪製失敗，可重試。',VIDEO_STORAGE_FAILED:'影片保存失敗，可重試。',
};

export function PodcastVideoStatus({state,error,busy,act,reload}:ReturnType<typeof usePodcastVideo>){
  const status=state?.status;
  return <div className="podcast-video-status" aria-live="polite">
    <span>{status==='ready'?'2D 教學影片已就緒':status==='pending'?'影片排隊中，可先收聽音訊':status==='running'?'正在製作教學影片，可先收聽音訊':status==='failed'?(reasons[state?.error_code??'']??'影片生成失敗，音訊仍可收聽。'):status==='cancelled'?'影片生成已取消':status==='unprepared'?'此集尚無教學影片':'音訊完成後會自動製作影片'}</span>
    {status==='unprepared'&&<button className="text-button" disabled={busy} onClick={()=>void act('prepare')}>補產生影片</button>}
    {['failed','cancelled'].includes(status??'')&&<button className="text-button" disabled={busy} onClick={()=>void act('retry')}>重試影片</button>}
    {['pending','running'].includes(status??'')&&<button className="text-button" disabled={busy} onClick={()=>void act('cancel')}>取消影片</button>}
    {error&&<p role="alert" className="form-error">{error}<button className="text-button" onClick={reload}>重新讀取影片狀態</button></p>}
  </div>;
}

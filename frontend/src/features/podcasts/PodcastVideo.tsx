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
  const act=async(action:'prepare'|'retry')=>{
    if(busy)return;setBusy(true);setError('');
    try{
      await api.studyTools(action==='prepare'?path:path+'/actions',action==='prepare'?{}:{action,expected_version:current?.version});
      if(alive.current)setReload(n=>n+1);
    }catch(e){if(alive.current)setError(errorMessage(e));}
    finally{if(alive.current)setBusy(false);}
  };
  return {state:current,error,busy,act,reload:()=>setReload(n=>n+1)};
}

import { useEffect, useRef, useState } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { InlineConfirm } from '../../ui/InlineConfirm';
import { Icon } from '../../ui/Icon';

export type VideoView = {
  schema: 'podcast-video/v1'; podcast_id: string; episode_index: number;
  // removed：使用者刪除了影片，本集改播音訊，逐字稿時間軸保留。
  status: 'waiting_audio' | 'unprepared' | 'pending' | 'running' | 'ready' | 'failed' | 'cancelled' | 'removed';
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

// 每集的「刪除影片」：只刪影片檔，音訊、逐字稿、時間軸與收聽進度保留，之後本集改播音訊。
export function DeleteVideoControl({ api, podcastId, index, onDeleted }: {
  api: StudydyApiClient; podcastId: string; index: number; onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  // 標題列右側的圖示按鈕；確認框在按鈕下方展開，不改變標題列高度。
  return <div className="podcast-delete-video">
    <button type="button" className="secondary-button icon-button" aria-label="刪除本集影片" title="刪除本集影片" aria-expanded={confirming} data-inline-confirm-trigger onClick={() => { setError(null); setConfirming(v => !v); }}><Icon name="trash" size={20} /></button>
    {confirming && <div className="podcast-delete-popover"><InlineConfirm message={`刪除第 ${index + 1} 集的影片？`} label="刪除影片確認"
    detail="只刪除影片檔、釋出空間；音訊、逐字稿與收聽進度都會保留，本集之後改播音訊。刪除後不會自動重新生成。"
    cancelLabel="保留影片" confirmLabel="刪除影片" busyLabel="正在刪除…" busy={busy} error={error}
    onCancel={() => setConfirming(false)} onConfirm={() => {
      setBusy(true); setError(null);
      void api.studyTools(`/v1/podcasts/${podcastId}/episodes/${index}/video`, undefined, undefined, 'DELETE').then(
        () => { setBusy(false); setConfirming(false); onDeleted(); },
        (e) => { setBusy(false); setError(errorMessage(e)); });
    }} /></div>}
  </div>;
}

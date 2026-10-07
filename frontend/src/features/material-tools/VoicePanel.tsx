import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { ApiClientError, errorMessage, type StudydyApiClient } from '../../api/client';
import { SourceButton, sourceLinks } from '../../ui/SourceButton';
import type { Conversation, VoiceTurn, VoiceView } from './types';
import { Icon } from '../../ui/Icon';
import type { PodcastContext } from '../../api/contracts';
import { AnswerAudio } from './AnswerAudio';
import { useDismissibleMenu } from '../material-flow/useDismissibleMenu';

const pending = new Set(['transcribing','pending','answering','speaking']);
const labels:Record<string,string> = { transcribing:'正在辨識你的問題…', pending:'等待教材回答…', answering:'正在依教材整理回答…', speaking:'正在產生語音…', failed:'本次處理未完成', cancelled:'已取消', draft:'這段錄音需要確認後送出' };

export function VoicePanel({api,materialId,revision,podcastContext,podcastMedia,onClose}:{
 api:StudydyApiClient;materialId:string;revision?:string;podcastContext?:PodcastContext;podcastMedia?:RefObject<HTMLMediaElement|null>;onClose:()=>void;
}) {
 const [list,setList]=useState<Conversation[]>([]),[id,setId]=useState<string|null>(null),[detail,setView]=useState<VoiceView|null>(null);
 const view=detail?.conversation_id===id?detail:null;
 const [question,setQuestion]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[recording,setRecording]=useState(false),[permission,setPermission]=useState(false),[reload,setReload]=useState(0);
 const [mode,setMode]=useState<'text'|'voice'>('text'),[draftQuestion,setDraftQuestion]=useState('');
 const alive=useRef(true),inFlight=useRef(false),version=useRef(0),readVersion=useRef(0),listVersion=useRef(0),current=useRef<string|null>(null);
 const recorder=useRef<MediaRecorder|null>(null),stream=useRef<MediaStream|null>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null),audios=useRef(new Set<HTMLAudioElement>()),chunks=useRef<Blob[]>([]);
 const transcript=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),draftLoaded=useRef(''),followLatest=useRef(true),focusAfterAction=useRef(false);
 const history=useRef<HTMLDetailsElement>(null),historyOpener=useRef<HTMLElement>(null);
 const createKey=useRef(crypto.randomUUID()),questionIntent=useRef<{body:string;key:string}|null>(null);
 const [autoPlayTurnId,setAutoPlayTurnId]=useState<string|null>(null);
 const expectedRevision=list.find(conversation=>conversation.conversation_id===id)?.knowledge_structure_revision??revision;
 useDismissibleMenu(history,historyOpener);
 const release=()=>{if(timer.current)clearTimeout(timer.current);stream.current?.getTracks().forEach(t=>t.stop());stream.current=null;};
 const pauseMedia=(except?:HTMLMediaElement)=>{for(const element of document.querySelectorAll<HTMLMediaElement>('audio, video'))if(element!==except)element.pause();podcastMedia?.current?.pause();};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;version.current++;readVersion.current++;if(recorder.current?.state==='recording')recorder.current.stop();release();for(const a of audios.current){a.pause();a.removeAttribute('src');a.load();}audios.current.clear();};},[]);
 useEffect(()=>{
   const playing=(event:Event)=>{if(event.target instanceof HTMLMediaElement&&!audios.current.has(event.target as HTMLAudioElement))for(const a of audios.current)a.pause();};
   document.addEventListener('play',playing,true);return()=>document.removeEventListener('play',playing,true);
 },[]);
 const refreshList=async()=>{
  const request=++listVersion.current;
  const result=await api.studyTools<{conversations:Conversation[]}>(`/v1/materials/${materialId}/voice-conversations`);
  if(alive.current&&request===listVersion.current)setList(result.conversations.filter(c=>!podcastContext||!revision||c.knowledge_structure_revision===revision));
 };
 useEffect(()=>{void refreshList().catch(e=>{if(alive.current)setError(errorMessage(e));});},[api,materialId,revision]);
 useEffect(()=>{
  if(!id)return;
  let stopped=false;let timeout:ReturnType<typeof setTimeout>;
  const read=async()=>{
   const request=++readVersion.current;
   try {
    const value=await api.studyTools<VoiceView>(`/v1/voice-conversations/${id}`);
    if(stopped||request!==readVersion.current||current.current!==id)return;
    if(value.conversation_id!==id||value.material_id!==materialId||(expectedRevision&&value.knowledge_structure_revision!==expectedRevision)){setView(null);setError('這段對話不屬於目前的教材版本。');return;}
    setView(value);
    const draft=value.turns.find(turn=>turn.status==='draft');
    if(draft&&draftLoaded.current!==draft.turn_id){draftLoaded.current=draft.turn_id;focusAfterAction.current=true;setDraftQuestion(draft.question);}
    else if(!draft&&draftLoaded.current&&value.turns.some(turn=>turn.turn_id===draftLoaded.current&&turn.status!=='draft')){draftLoaded.current='';setDraftQuestion('');}
    if(value.turns.some(turn=>pending.has(turn.status)))timeout=setTimeout(read,1800);
   }catch(e){if(!stopped&&request===readVersion.current)setError(errorMessage(e));}
  };
  void read();return()=>{stopped=true;clearTimeout(timeout);for(const audio of audios.current)audio.pause();};
 },[api,id,reload,materialId,expectedRevision]);
 const choose=(next:string|null)=>{
  version.current++;readVersion.current++;current.current=next;
  for(const audio of audios.current)audio.pause();
  setId(next);setView(null);setQuestion('');setDraftQuestion('');setError('');draftLoaded.current='';followLatest.current=true;
  setAutoPlayTurnId(null);
  if(history.current)history.current.open=false;
  if(inFlight.current)focusAfterAction.current=true;else input.current?.focus();
 };
 const switchMode=(next:'text'|'voice')=>{
  if(recording||permission)return;
  version.current++;setMode(next);setAutoPlayTurnId(null);
  for(const audio of audios.current)audio.pause();
  if(next==='voice')pauseMedia();else focusAfterAction.current=true;
 };
 const create=async()=>{
  const value=await api.studyTools<Conversation>(`/v1/materials/${materialId}/voice-conversations`,revision?{knowledge_structure_revision:revision}:{},createKey.current);
  if(!alive.current)return null;
  if(value.material_id!==materialId||(revision&&value.knowledge_structure_revision!==revision))throw new ApiClientError('schema','這段對話不屬於目前的教材版本。',{reasonCode:'RESPONSE_SCHEMA_MISMATCH'});
  createKey.current=crypto.randomUUID();current.current=value.conversation_id;setId(value.conversation_id);setView(null);
  await refreshList();return value.conversation_id;
 };
 const perform=async(fn:()=>Promise<unknown>)=>{
  if(inFlight.current)return;
  inFlight.current=true;readVersion.current++;setBusy(true);setError('');
  try{await fn();}catch(e){if(alive.current)setError(errorMessage(e));}
  finally{inFlight.current=false;if(alive.current){setBusy(false);setReload(value=>value+1);}}
 };
 const send=()=>{
  const draft=view?.turns.find(turn=>turn.status==='draft'),text=draft?draftQuestion:question;
  if(!text.trim())return;
  setAutoPlayTurnId(draft&&mode==='voice'?draft.turn_id:null);
  followLatest.current=true;focusAfterAction.current=true;
  void perform(async()=>{
   const active=id??await create();if(!active)return;
   if(draft){
    await api.studyTools(`/v1/voice-conversations/${active}/turns/${draft.turn_id}/actions`,{action:'send',question:text});
    if(alive.current)setView(previous=>previous?.conversation_id===active?{...previous,turns:previous.turns.map(turn=>turn.turn_id===draft.turn_id?{...turn,status:'pending',question:text}:turn)}:previous);
   }else{
    const body=JSON.stringify({active,question:text,context:podcastContext});
    if(questionIntent.current?.body!==body)questionIntent.current={body,key:crypto.randomUUID()};
    await api.studyTools(`/v1/voice-conversations/${active}/turns`,{question:text,...(podcastContext?{context:podcastContext}:{})},questionIntent.current.key);
    questionIntent.current=null;
   }
   if(alive.current&&current.current===active){draftLoaded.current='';if(draft)setDraftQuestion('');else setQuestion('');}
   await refreshList();
  });
 };
 const turnAction=(turn:VoiceTurn,action:'retry'|'cancel')=>void perform(async()=>{
  const generation=version.current;
  if(action==='cancel')setAutoPlayTurnId(previous=>previous===turn.turn_id?null:previous);
  const result=await api.studyTools<{status?:string}>(`/v1/voice-conversations/${id}/turns/${turn.turn_id}/actions`,{action});
  if(alive.current&&current.current===id){
   if(action==='retry'&&generation===version.current&&mode==='voice'&&(turn.mode==='voice'||(turn.mode==null&&turn.answer)))setAutoPlayTurnId(turn.turn_id);
   if(action==='cancel')setAutoPlayTurnId(previous=>previous===turn.turn_id?null:previous);
   if(turn.status==='draft'){draftLoaded.current='';setDraftQuestion('');focusAfterAction.current=true;}
   if(typeof result.status==='string'&&(result.status==='ready'||Object.hasOwn(labels,result.status)))setView(previous=>previous?.conversation_id===id?{...previous,turns:previous.turns.map(item=>item.turn_id===turn.turn_id?{...item,status:result.status!}:item)}:previous);
  }
 });
 const start=async()=>{pauseMedia();const generation=++version.current;setPermission(true);setError('');try{
 if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('此瀏覽器無法錄音，請使用文字提問。');
 const s=await navigator.mediaDevices.getUserMedia({audio:true});if(!alive.current||generation!==version.current){s.getTracks().forEach(t=>t.stop());return;}stream.current=s;
 const r=new MediaRecorder(s);recorder.current=r;chunks.current=[];
 r.ondataavailable=e=>{if(alive.current&&generation===version.current&&e.data.size)chunks.current.push(e.data);};
 r.onstop=()=>{if(!alive.current||generation!==version.current){s.getTracks().forEach(t=>t.stop());return;}release();setRecording(false);const blob=new Blob(chunks.current,{type:r.mimeType});void perform(async()=>{const active=id??await create();if(active){const turnId=await api.voiceRecording(active,blob,crypto.randomUUID(),podcastContext);if(alive.current&&generation===version.current&&current.current===active){setAutoPlayTurnId(turnId);followLatest.current=true;}}});};
 r.onerror=()=>{if(!alive.current||generation!==version.current){s.getTracks().forEach(t=>t.stop());return;}version.current++;release();setRecording(false);setError('錄音中斷，請再試一次或改用文字。');};
 r.start();setRecording(true);timer.current=setTimeout(()=>{if(r.state==='recording')r.stop();},120000);
 }catch(e){if(alive.current&&generation===version.current){release();setError(e instanceof Error?e.message:'無法取得麥克風');}}finally{if(alive.current&&generation===version.current)setPermission(false);}};
 const cancelRecording=()=>{focusAfterAction.current=true;version.current++;if(recorder.current?.state==='recording')recorder.current.stop();release();setRecording(false);setPermission(false);};
 useEffect(()=>{
  let path=window.location.pathname;
  const changed=()=>{
   if(path===window.location.pathname)return;
   path=window.location.pathname;version.current++;
   if(recorder.current?.state==='recording')recorder.current.stop();
   release();setRecording(false);setPermission(false);setAutoPlayTurnId(null);
   for(const audio of audios.current)audio.pause();
  };
  window.addEventListener('popstate',changed);return()=>window.removeEventListener('popstate',changed);
 },[]);

 const draft=view?.turns.find(turn=>turn.status==='draft');
 const working=view?.turns.some(turn=>pending.has(turn.status))??false;
 useEffect(()=>{
  if(autoPlayTurnId&&view?.turns.some(turn=>turn.turn_id===autoPlayTurnId&&['failed','cancelled'].includes(turn.status)))setAutoPlayTurnId(null);
 },[view,autoPlayTurnId]);
 useEffect(()=>{if(!busy&&!recording&&!permission&&focusAfterAction.current){focusAfterAction.current=false;input.current?.focus();}},[busy,recording,permission,mode,draft]);
 const turns=view?.turns.filter(turn=>turn.status!=='draft')??[];
 const lastTurn=turns.at(-1);
 useLayoutEffect(()=>{
  const element=input.current;if(element){element.style.height='auto';element.style.height=`${element.scrollHeight}px`;}
  if(followLatest.current&&transcript.current)transcript.current.scrollTop=transcript.current.scrollHeight;
 },[question,draftQuestion,mode]);
 useLayoutEffect(()=>{
  if(followLatest.current&&transcript.current)transcript.current.scrollTop=transcript.current.scrollHeight;
 },[id,lastTurn?.turn_id,lastTurn?.status,lastTurn?.answer?.text,lastTurn?.audio_url]);
 return <div className="voice-panel" onKeyDown={event=>{
  if(event.key==='Escape'&&history.current?.open){event.stopPropagation();event.preventDefault();history.current.open=false;historyOpener.current?.focus();}
 }}>
  <header className="voice-header"><div className="voice-identity"><Icon name="microphone" size={18}/><h2>教材問答</h2></div>
   <div className="voice-header-actions">
    <details className="voice-history-menu" ref={history}><summary ref={historyOpener} role="button" aria-label="對話紀錄" title="對話紀錄"><Icon name="clock" size={18}/></summary>
     <div className="voice-conversation-popover"><label>已保存的對話<select aria-label="已保存的對話" value={id??''} disabled={busy||recording||permission} onChange={event=>choose(event.target.value||null)}><option value="">新的教材問答</option>{list.map(c=><option key={c.conversation_id} value={c.conversation_id}>{c.title}</option>)}</select></label>
      {id&&<button className="text-button voice-delete" disabled={busy||recording||permission} onClick={()=>{
       if(window.confirm('刪除這段對話及語音？教材會保留。'))void perform(async()=>{await api.studyTools(`/v1/voice-conversations/${id}`,undefined,undefined,'DELETE');if(alive.current){choose(null);await refreshList();}});
      }}>刪除對話</button>}
     </div>
    </details>
    <button className="voice-icon-button" type="button" aria-label="新對話" title="新對話" disabled={busy||recording||permission} onClick={()=>choose(null)}><span aria-hidden="true">＋</span></button>
    <button className="voice-icon-button" type="button" aria-label="關閉" title="關閉" onClick={onClose}><span aria-hidden="true">×</span></button>
   </div>
  </header>
  <div className="voice-modes" role="group" aria-label="提問模式">
   <button type="button" aria-pressed={mode==='text'} disabled={recording||permission} onClick={()=>switchMode('text')}>文字模式</button>
   <button type="button" aria-pressed={mode==='voice'} disabled={recording||permission} onClick={()=>switchMode('voice')}>語音模式</button>
  </div>
  {view&&!view.is_current_revision&&<p className="voice-revision-note">使用先前的教材版本。開啟新對話可使用更新後內容。</p>}
  <div className="voice-turns" ref={transcript} aria-live="polite" aria-relevant="additions text" tabIndex={0} aria-label="教材問答紀錄" onScroll={event=>{
   const element=event.currentTarget;followLatest.current=element.scrollHeight-element.clientHeight-element.scrollTop<48;
  }}>
   {(!id||view?.turns.length===0)&&<div className="voice-empty"><h3>教材裡哪個地方想再了解？</h3><p>{mode==='voice'?'錄音後會直接辨識問題並以語音回答。':'輸入問題，文字回答會附上教材來源。'}</p>{mode==='text'&&<div>{['主要觀念是什麼？','解釋最重要的概念'].map(text=><button type="button" className="voice-suggestion" key={text} onClick={()=>{setQuestion(text);input.current?.focus();}}>{text}</button>)}</div>}</div>}
   {id&&!view&&<p className="voice-inline-note" role="status">正在讀取對話…</p>}
   {turns.map(turn=>{
    const sources=sourceLinks(turn.answer?.citations.flatMap(c=>c.evidence)??[]);
    if(turn.status==='cancelled'&&!turn.answer)return <article className="voice-turn voice-cancelled" key={turn.turn_id}><small>已取消這次提問</small></article>;
    return <article key={turn.turn_id} className="voice-turn">
     <div className="voice-question" aria-label="你的提問">{turn.context&&<small>Podcast 第 {turn.context.episode_index+1} 集 · 所選段落</small>}{turn.mode==='voice'&&turn.question&&<small>辨識的問題</small>}<p>{turn.question||'語音提問'}</p></div>
     <div className="voice-response"><span className="voice-response-label">教材回答</span>
      {turn.answer&&<><p className="voice-answer">{turn.answer.text}</p><div className="voice-answer-actions">
       {turn.audio_url&&<AnswerAudio src={turn.audio_url} autoPlay={mode==='voice'&&autoPlayTurnId===turn.turn_id} onAutoPlayAttempt={()=>setAutoPlayTurnId(previous=>previous===turn.turn_id?null:previous)} register={element=>audios.current.add(element)} onPlay={element=>{if(recording)element.pause();else pauseMedia(element);}}/>}
       <details className="voice-sources"><summary>查看來源 · {sources.length}</summary><div className="voice-source-list">{sources.map(source=><SourceButton key={source.evidence_id} apiClient={api} resolver={view!.source_resolver} evidence={source}/>)}{!sources.length&&<p>教材沒有足夠依據。</p>}</div></details>
      </div></>}
      {turn.status!=='ready'&&<div className="voice-turn-status">{pending.has(turn.status)&&<span className="voice-wait-dot" aria-hidden="true"/>}<small>{turn.status==='failed'&&turn.answer?'文字回答已完成':labels[turn.status]??'處理中…'}</small>
       {pending.has(turn.status)&&<button className="text-button" disabled={busy} onClick={()=>turnAction(turn,'cancel')}>取消這次提問</button>}
      </div>}
      {turn.status==='failed'&&<><p className="voice-inline-note">{turn.answer?'語音製作未完成，文字與來源已保留。':'目前無法完成處理。'}{turn.error_code==='VOICE_TRANSCRIPT_INVALID'?'沒有辨識到清楚語音，請重新錄音。':turn.error_code==='VOICE_PODCAST_CONTEXT_INVALID'||turn.error_code==='RESOURCE_NOT_FOUND'?'原 Podcast 已不可用，請改用一般教材問答。':''}</p>{turn.question&&<button className="text-button" disabled={busy||working||!!draft} onClick={()=>turnAction(turn,'retry')}>重試{turn.answer?'語音':'回答'}</button>}</>}
     </div>
    </article>;
   })}
  </div>
  <form className="voice-composer" onSubmit={event=>{event.preventDefault();if(!busy&&!working&&!recording&&!permission)send();}}>
   {error&&<p role="alert" className="form-error">{error}</p>}
   <div className="voice-composer-status" role="status">
    {recording?<><span><span className="recording-dot"/>正在錄音，最長兩分鐘</span><button className="text-button" type="button" onClick={cancelRecording}>取消錄音</button></>
     :permission?<><span>等待麥克風…</span><button className="text-button" type="button" onClick={cancelRecording}>取消等待麥克風</button></>
     :draft?<><span className="voice-draft-hint">這段錄音需要確認後送出</span><button className="text-button" type="button" disabled={busy} onClick={()=>turnAction(draft,'cancel')}>取消這次提問</button></>
     :<span>{busy?'正在送出…':mode==='voice'?'停止錄音後會自動送出問題':'文字回答僅依據這份教材'}</span>}
   </div>
   {(mode==='text'||draft)?<div className="voice-composer-row">
    <textarea ref={input} className="voice-composer-input" aria-label="問題／辨識文字" rows={1} maxLength={4000} value={draft?draftQuestion:question} disabled={busy||recording||permission} onChange={event=>draft?setDraftQuestion(event.target.value):setQuestion(event.target.value)} placeholder="輸入問題……" title="Enter 送出，Shift+Enter 換行" onKeyDown={event=>{
     if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.keyCode!==229){event.preventDefault();if(!busy&&!working&&!recording&&!permission)send();}
    }}/>
    <button className="voice-send" type="submit" aria-label="送出問題" title="送出問題" disabled={busy||working||recording||permission||!(draft?draftQuestion:question).trim()}><Icon name={busy?'process':'chevron-right'} size={20}/></button>
   </div>:<button className={`voice-record-button${recording?' is-recording':''}`} type="button" disabled={!recording&&(busy||working||permission)} onClick={()=>{if(recording)recorder.current?.stop();else void start();}}><Icon name="microphone" size={19}/>{recording?'停止錄音並送出':'開始錄音'}</button>}
  </form>
 </div>;
}

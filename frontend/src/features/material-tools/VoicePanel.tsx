import { useEffect, useRef, useState } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import { SourceButton, sourceLinks } from '../../ui/SourceButton';
import type { Conversation, VoiceView } from './types';
import { Icon } from '../../ui/Icon';
import { AnswerAudio } from './AnswerAudio';

const pending = new Set(['transcribing','pending','answering','speaking']);
const labels:Record<string,string>={transcribing:'辨識錄音中',pending:'等待回答',answering:'整理回答中',speaking:'製作語音中',failed:'本次處理未完成',cancelled:'已取消',draft:'請確認辨識文字',ready:'已完成'};

export function VoicePanel({api,materialId}:{api:StudydyApiClient;materialId:string}){
 const [list,setList]=useState<Conversation[]>([]),[id,setId]=useState<string|null>(null),[view,setView]=useState<VoiceView|null>(null);
 const [question,setQuestion]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[recording,setRecording]=useState(false),[permission,setPermission]=useState(false);
 const alive=useRef(true),version=useRef(0),recorder=useRef<MediaRecorder|null>(null),stream=useRef<MediaStream|null>(null),timer=useRef<ReturnType<typeof setTimeout>|null>(null),audios=useRef(new Set<HTMLAudioElement>()),chunks=useRef<Blob[]>([]);
 const transcript=useRef<HTMLDivElement>(null),draftLoaded=useRef('');
 const createKey=useRef(crypto.randomUUID()),questionIntent=useRef<{body:string;key:string}|null>(null);
 const release=()=>{if(timer.current)clearTimeout(timer.current);stream.current?.getTracks().forEach(t=>t.stop());stream.current=null;};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;version.current++;if(recorder.current?.state==='recording')recorder.current.stop();release();for(const a of audios.current){a.pause();a.removeAttribute('src');a.load();}audios.current.clear();};},[]);
 const refreshList=async()=>{const d=await api.studyTools<{conversations:Conversation[]}>(`/v1/materials/${materialId}/voice-conversations`);if(alive.current)setList(d.conversations);};
 useEffect(()=>{void refreshList().catch(e=>setError(errorMessage(e)));},[api,materialId]);
 useEffect(()=>{setView(previous=>previous?.conversation_id===id?previous:null);if(!id)return;let stopped=false;let t:ReturnType<typeof setTimeout>;
 const read=async()=>{try{const v=await api.studyTools<VoiceView>(`/v1/voice-conversations/${id}`);if(stopped)return;setView(v);const draft=v.turns.find(x=>x.status==='draft');if(draft&&draftLoaded.current!==draft.turn_id){draftLoaded.current=draft.turn_id;setQuestion(draft.question);}if(v.turns.some(x=>pending.has(x.status)))t=setTimeout(read,1800);}catch(e){if(!stopped)setError(errorMessage(e));}};
 void read();return()=>{stopped=true;clearTimeout(t);for(const a of audios.current)a.pause();};},[api,id,busy]);
 const create=async()=>{const c=await api.studyTools<Conversation>(`/v1/materials/${materialId}/voice-conversations`,{},createKey.current);if(!alive.current)return null;createKey.current=crypto.randomUUID();setId(c.conversation_id);setView(null);setQuestion('');await refreshList();return c.conversation_id;};
 const perform=async(fn:()=>Promise<unknown>)=>{if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){if(alive.current)setError(errorMessage(e));}finally{if(alive.current)setBusy(false);}};
 const send=()=>perform(async()=>{const active=id??await create();if(!active)return;const draft=view?.turns.find(t=>t.status==='draft');
 if(draft)await api.studyTools(`/v1/voice-conversations/${active}/turns/${draft.turn_id}/actions`,{action:'send',question});
 else {const body=JSON.stringify({active,question});if(questionIntent.current?.body!==body)questionIntent.current={body,key:crypto.randomUUID()};await api.studyTools(`/v1/voice-conversations/${active}/turns`,{question},questionIntent.current.key);questionIntent.current=null;}setQuestion('');await refreshList();});
 const start=async()=>{const generation=++version.current;setPermission(true);setError('');try{
 if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('此瀏覽器無法錄音，請使用文字提問。');
 const s=await navigator.mediaDevices.getUserMedia({audio:true});if(!alive.current||generation!==version.current){s.getTracks().forEach(t=>t.stop());return;}stream.current=s;
 const r=new MediaRecorder(s);recorder.current=r;chunks.current=[];
 r.ondataavailable=e=>{if(alive.current&&generation===version.current&&e.data.size)chunks.current.push(e.data);};
 r.onstop=()=>{if(!alive.current||generation!==version.current){s.getTracks().forEach(t=>t.stop());return;}release();setRecording(false);const blob=new Blob(chunks.current,{type:r.mimeType});void perform(async()=>{const active=id??await create();if(active)await api.voiceRecording(active,blob,crypto.randomUUID());});};
 r.onerror=()=>{if(!alive.current||generation!==version.current){s.getTracks().forEach(t=>t.stop());return;}version.current++;release();setRecording(false);setError('錄音中斷，請再試一次或改用文字。');};
 r.start();setRecording(true);timer.current=setTimeout(()=>{if(r.state==='recording')r.stop();},120000);
 }catch(e){if(alive.current&&generation===version.current){release();setError(e instanceof Error?e.message:'無法取得麥克風');}}finally{if(alive.current&&generation===version.current)setPermission(false);}};
 const cancelRecording=()=>{version.current++;if(recorder.current?.state==='recording')recorder.current.stop();release();setRecording(false);setPermission(false);};
 const working=view?.turns.some(t=>pending.has(t.status))??false;
 const lastTurn=view?.turns.at(-1);
 useEffect(()=>{const element=transcript.current;if(element)element.scrollTop=element.scrollHeight;},[id,lastTurn?.turn_id,lastTurn?.status]);
 return <div className="voice-panel">
 <div className="voice-history"><label className="tool-field"><span className="visually-hidden">已保存的對話</span><select aria-label="已保存的對話" value={id??''} disabled={busy||recording||permission} onChange={e=>{version.current++;setId(e.target.value||null);setQuestion('');setError('');draftLoaded.current='';}}><option value="">新的教材問答</option>{list.map(c=><option key={c.conversation_id} value={c.conversation_id}>{c.title}</option>)}</select></label>
 <button className="secondary-button" disabled={busy||recording||permission} onClick={()=>{setId(null);setQuestion('');draftLoaded.current='';}}>新對話</button>
 {id&&<details className="voice-management"><summary aria-label="對話管理">⋯</summary><button className="text-button" disabled={busy||recording} onClick={()=>{if(window.confirm('刪除這段對話及語音？教材會保留。'))void perform(async()=>{await api.studyTools(`/v1/voice-conversations/${id}`,undefined,undefined,'DELETE');setId(null);await refreshList();});}}>刪除對話</button></details>}</div>
 {view&&!view.is_current_revision&&<p className="tool-notice">這段對話使用先前的教材內容。要使用更新後的教材，請開啟新對話。</p>}
 <div className="voice-turns" ref={transcript} aria-live="polite" tabIndex={0} aria-label="教材問答紀錄">
 {(!id||view?.turns.length===0)&&<div className="voice-empty"><span><Icon name="microphone" size={28}/></span><h3>教材裡哪個地方想再了解？</h3><p>錄下你的問題，或直接輸入文字。回答會附上教材來源。</p><div>{['這份教材的主要觀念是什麼？','請解釋這份教材最重要的概念。'].map(text=><button className="voice-suggestion" key={text} onClick={()=>setQuestion(text)}>{text}</button>)}</div></div>}
 {id&&!view&&<p className="tool-description" role="status">正在讀取對話…</p>}
 {view?.turns.map(t=><article key={t.turn_id} className="voice-turn"><div className="voice-question"><span>你</span><p>{t.question||'語音提問'}</p></div>
 <div className="voice-response"><span className="voice-response-label"><Icon name="learning" size={16}/>教材回答</span>
 {t.answer&&<><p className="voice-answer">{t.answer.text}</p>{t.audio_url&&<AnswerAudio src={t.audio_url} register={el=>audios.current.add(el)} onPlay={element=>{for(const other of audios.current)if(other!==element)other.pause();}}/>}
 <details><summary>查看回答來源</summary>{sourceLinks(t.answer.citations.flatMap(c=>c.evidence)).map(e=><SourceButton key={e.evidence_id} apiClient={api} resolver={view.source_resolver} evidence={e}/>)}{!t.answer.citations.length&&<p>教材沒有足夠依據。</p>}</details></>}
 {t.status!=='ready'&&<div className="voice-turn-status"><small>{labels[t.status]??t.status}</small>
 {(pending.has(t.status)||t.status==='draft')&&<button className="text-button" disabled={busy} onClick={()=>void perform(()=>api.studyTools(`/v1/voice-conversations/${id}/turns/${t.turn_id}/actions`,{action:'cancel'}))}>取消這次提問</button>}</div>}
 {t.status==='failed'&&<><p className="tool-description">目前無法完成{t.answer?'語音；文字回答已保留':'處理'}。{t.error_code==='VOICE_TRANSCRIPT_INVALID'?'沒有辨識到清楚語音，請重新錄音。':''}</p>{t.question&&<button className="secondary-button" disabled={busy||working} onClick={()=>void perform(()=>api.studyTools(`/v1/voice-conversations/${id}/turns/${t.turn_id}/actions`,{action:'retry'}))}>重試{t.answer?'語音':'回答'}</button>}</>}
 </div></article>)}</div>
 <div className="voice-composer">
 {error&&<p role="alert" className="form-error">{error}</p>}
 {view?.turns.some(t=>t.status==='draft')&&<p className="voice-draft-hint">辨識完成，可修改文字後送出。</p>}
 <label className="tool-field"><span className="visually-hidden">問題／辨識文字</span><textarea aria-label="問題／辨識文字" rows={3} maxLength={4000} value={question} disabled={recording||permission||working} onChange={e=>setQuestion(e.target.value)} placeholder="輸入問題，或按麥克風錄音…"/></label>
 <div className="voice-composer-actions"><div>{recording?<><button className="secondary-button is-recording" onClick={()=>recorder.current?.stop()}><span className="recording-dot"/>停止錄音並辨識</button><button className="text-button" onClick={cancelRecording}>取消錄音</button></>:permission?<button className="secondary-button" onClick={cancelRecording}>取消等待麥克風</button>:<button className="secondary-button" disabled={busy||working||view?.turns.some(t=>t.status==='draft')} onClick={()=>void start()}><Icon name="microphone" size={17}/>開始錄音</button>}</div>
 <button className="primary-button" disabled={busy||working||recording||permission||!question.trim()} onClick={()=>void send()}>{busy?'處理中…':'送出問題'}<Icon name="chevron-right" size={16}/></button></div>
 <p className="voice-composer-note" role={recording?'status':undefined}>{recording?'正在錄音，最長兩分鐘。停止後可以修改辨識文字再送出。':'回答僅依據這份教材，對話會保存在帳號中。'}</p>
 </div></div>;
}

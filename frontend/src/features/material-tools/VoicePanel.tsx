import { MathText } from "../../ui/MathText";
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { InlineConfirm } from '../../ui/InlineConfirm';
import { ApiClientError, errorMessage, type StudydyApiClient } from '../../api/client';
import { SourceButton } from '../../ui/SourceButton';
import type { Conversation, VoiceTurn, VoiceView } from './types';
import { Icon } from '../../ui/Icon';
import type { KnowledgeOutlineView, PodcastContext } from '../../api/contracts';
import { answerSources } from './answerSources';
import { VoiceAnswerAudio } from './VoiceAnswerAudio';
import { prefetchVoiceAssets, useContinuousVoice } from './useContinuousVoice';

const pending = new Set(['transcribing','pending','answering','speaking']);
const labels:Record<string,string> = { transcribing:'正在辨識你的問題…', pending:'等待教材回答…', answering:'正在依教材整理回答…', speaking:'正在產生語音…', failed:'本次處理未完成', cancelled:'已取消' };
const stageLabels:Record<string,string> = {retrieval:'正在查找教材依據…',answering:'正在整理回答…',checking:'正在核對回答來源…',tts_queued:'正在等待語音製作…',tts_synthesizing:'正在準備語音…'};
const mascot = '/assets/Studydy_角色素材/引導/guide_present.png';

/** 建議問題只用知識結構裡真實存在的概念名稱與關係，句型固定，不由模型生成。 */
export function suggestedQuestions(outline: Pick<KnowledgeOutlineView,'concepts'|'relations'|'initial_learning_path'>): string[] {
 const names = new Map(outline.concepts.map(concept => [concept.concept_id, concept.label.trim()]));
 const known = (id: string) => Boolean(names.get(id));
 const path = [...outline.initial_learning_path].sort((a, b) => a.position - b.position).map(step => step.concept_id).filter(known);
 const ranked = [...outline.concepts].sort((a, b) => b.claim_count - a.claim_count).map(concept => concept.concept_id).filter(known);
 const first = path[0] ?? ranked[0];
 if (!first) return [];
 const questions = [`什麼是「${names.get(first)}」？`];
 const linked = (relation: KnowledgeOutlineView['relations'][number]) => relation.source_concept_id !== relation.target_concept_id && known(relation.source_concept_id) && known(relation.target_concept_id);
 const relation = outline.relations.find(item => linked(item) && (item.source_concept_id === first || item.target_concept_id === first)) ?? outline.relations.find(linked);
 if (relation) questions.push(`「${names.get(relation.source_concept_id)}」和「${names.get(relation.target_concept_id)}」有什麼關係？`);
 const used = new Set([first, relation?.source_concept_id, relation?.target_concept_id]);
 const other = ranked.find(id => !used.has(id));
 if (other) questions.push(`「${names.get(other)}」的重點有哪些？`);
 return questions;
}

export function VoicePanel({api,materialId,revision,podcastContext,podcastMedia,onClose}:{
 api:StudydyApiClient;materialId:string;revision?:string;podcastContext?:PodcastContext;podcastMedia?:RefObject<HTMLMediaElement|null>;onClose:()=>void;
}) {
 const [list,setList]=useState<Conversation[]>([]),[id,setId]=useState<string|null>(null),[detail,setView]=useState<VoiceView|null>(null);
 const view=detail?.conversation_id===id?detail:null;
 const [question,setQuestion]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
 // 刪除對話在對話紀錄面板內原位置確認；換到別段對話時收起。
 const [confirmingDelete,setConfirmingDelete]=useState(false);
 useEffect(()=>setConfirmingDelete(false),[id]);
 const [mode,setMode]=useState<'text'|'voice'>('text');
 const [outgoing,setOutgoing]=useState<{text:string;after:number}|null>(null),[historyOpen,setHistoryOpen]=useState(false),[behind,setBehind]=useState(false),[suggestions,setSuggestions]=useState<string[]>([]);
 const alive=useRef(true),inFlight=useRef(false),version=useRef(0),readVersion=useRef(0),listVersion=useRef(0),current=useRef<string|null>(null);
 const audios=useRef(new Set<HTMLAudioElement>());
 const transcript=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),followLatest=useRef(true),focusAfterAction=useRef(false);
 const historyOpener=useRef<HTMLButtonElement>(null),historyPanel=useRef<HTMLDivElement>(null);
 const createKey=useRef(crypto.randomUUID()),questionIntent=useRef<{body:string;key:string}|null>(null);
 const [autoPlayTurnId,setAutoPlayTurnId]=useState<string|null>(null);
 const [activeAnswer,setActiveAnswer]=useState<HTMLMediaElement|null>(null);
 const expectedRevision=list.find(conversation=>conversation.conversation_id===id)?.knowledge_structure_revision??revision;
 const pauseMedia=(except?:HTMLMediaElement)=>{setActiveAnswer(except??null);for(const element of document.querySelectorAll<HTMLMediaElement>('audio, video'))if(element!==except)element.pause();podcastMedia?.current?.pause();};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;version.current++;readVersion.current++;for(const a of audios.current){a.pause();a.removeAttribute('src');a.load();}audios.current.clear();};},[]);
 useEffect(()=>{
   const playing=(event:Event)=>{
    if(event.target instanceof HTMLMediaElement&&!audios.current.has(event.target as HTMLAudioElement)){
     setActiveAnswer(null);for(const a of audios.current)a.pause();
    }
   };
   document.addEventListener('play',playing,true);return()=>document.removeEventListener('play',playing,true);
 },[]);
 // 建議問題讀取失敗只是不顯示，不影響問答。
 useEffect(()=>{
  if(!expectedRevision)return;
  let stopped=false;
  void api.getKnowledgeOutline({materialId,structureRevision:expectedRevision}).then(outline=>{if(!stopped)setSuggestions(suggestedQuestions(outline));},()=>undefined);
  return()=>{stopped=true;};
 },[api,materialId,expectedRevision]);
 const refreshList=async()=>{
  const request=++listVersion.current;
  const result=await api.studyTools<{conversations:Conversation[]}>(`/v1/materials/${materialId}/voice-conversations`);
  if(alive.current&&request===listVersion.current)setList(result.conversations.filter(c=>!podcastContext||!revision||c.knowledge_structure_revision===revision));
 };
 useEffect(()=>{void refreshList().catch(e=>{if(alive.current)setError(errorMessage(e));});},[api,materialId,revision]);
 useEffect(()=>{
  if(!id)return;
  let stopped=false;let readFailed=false;let timeout:ReturnType<typeof setTimeout>;
  const read=async()=>{
   const request=++readVersion.current;
   try {
    const value=await api.studyTools<VoiceView>(`/v1/voice-conversations/${id}`);
    if(stopped||request!==readVersion.current||current.current!==id)return;
    if(value.conversation_id!==id||value.material_id!==materialId||(expectedRevision&&value.knowledge_structure_revision!==expectedRevision)){setView(null);setError('這段對話不屬於目前的教材版本。');return;}
    setView(value);
    if(readFailed){readFailed=false;setError('');}
    if(value.turns.some(turn=>pending.has(turn.status)))timeout=setTimeout(read,1800);
   }catch(e){
    if(stopped||request!==readVersion.current)return;
    setError(errorMessage(e));
    // 暫時斷線只重讀已保存的對話，不能重送問題或讓「處理中」永久卡住。
    if(e instanceof ApiClientError&&(e.kind==='network'||(e.status??0)>=500)){
     readFailed=true;timeout=setTimeout(read,3000);
    }
   }
  };
  void read();return()=>{stopped=true;clearTimeout(timeout);for(const audio of audios.current)audio.pause();};
 },[api,id,reload,materialId,expectedRevision]);
 const closeHistory=(focusOpener:boolean)=>{setHistoryOpen(false);if(focusOpener)historyOpener.current?.focus();};
 const choose=(next:string|null)=>{
  version.current++;readVersion.current++;current.current=next;setActiveAnswer(null);
  for(const audio of audios.current)audio.pause();
  setId(next);setView(null);setQuestion('');setError('');setOutgoing(null);followLatest.current=true;setBehind(false);
  setReload(value=>value+1);
  setAutoPlayTurnId(null);
  setHistoryOpen(false);
  if(inFlight.current)focusAfterAction.current=true;else input.current?.focus();
 };
 const switchMode=(next:'text'|'voice')=>{
  version.current++;setMode(next);setAutoPlayTurnId(null);setActiveAnswer(null);
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
  try{await fn();}catch(e){if(alive.current){setError(errorMessage(e));setOutgoing(null);}}
  finally{inFlight.current=false;if(alive.current){setBusy(false);setReload(value=>value+1);}}
 };
 const send=()=>{
  const text=question;
  if(!text.trim())return;
  pauseMedia();setAutoPlayTurnId(null);
  followLatest.current=true;setBehind(false);focusAfterAction.current=true;
  // 送出後立刻顯示自己的訊息；讀到已保存的這一輪後改由對話紀錄呈現。
  setOutgoing({text,after:view?.turns.length??0});
  void perform(async()=>{
   const active=id??await create();if(!active)return;
    const body=JSON.stringify({active,question:text,mode,context:podcastContext});
    if(questionIntent.current?.body!==body)questionIntent.current={body,key:crypto.randomUUID()};
    const result=await api.studyTools<{turn_id:string}>(`/v1/voice-conversations/${active}/turns`,{question:text,mode,...(podcastContext?{context:podcastContext}:{})},questionIntent.current.key);
    if(alive.current&&mode==='voice'&&current.current===active)setAutoPlayTurnId(result.turn_id);
    questionIntent.current=null;
   if(alive.current&&current.current===active)setQuestion('');
   await refreshList();
  });
 };
 const turnAction=(turn:VoiceTurn,action:'retry'|'cancel')=>void perform(async()=>{
  const generation=version.current;
  if(action==='cancel')setAutoPlayTurnId(previous=>previous===turn.turn_id?null:previous);
  const result=await api.studyTools<{status?:string}>(`/v1/voice-conversations/${id}/turns/${turn.turn_id}/actions`,{action});
  if(alive.current&&current.current===id){
   if(action==='retry'&&generation===version.current&&mode==='voice'&&turn.mode==='voice')setAutoPlayTurnId(turn.turn_id);
   if(action==='cancel')setAutoPlayTurnId(previous=>previous===turn.turn_id?null:previous);
   if(typeof result.status==='string'&&(result.status==='ready'||Object.hasOwn(labels,result.status)))setView(previous=>previous?.conversation_id===id?{...previous,turns:previous.turns.map(item=>item.turn_id===turn.turn_id?{...item,status:result.status!}:item)}:previous);
  }
 });
 useEffect(()=>{
  const changed=()=>{setMode('text');setAutoPlayTurnId(null);setActiveAnswer(null);for(const audio of audios.current)audio.pause();};
  window.addEventListener('popstate',changed);return()=>window.removeEventListener('popstate',changed);
 },[]);
 useEffect(()=>{if(historyOpen)historyPanel.current?.querySelector<HTMLElement>('[aria-current="true"], button')?.focus();},[historyOpen]);

 const working=view?.turns.some(turn=>pending.has(turn.status))??false;
 const listening=useContinuousVoice({enabled:mode==='voice',paused:busy||working,
  onSpeech:()=>{setActiveAnswer(null);for(const audio of audios.current)audio.pause();setAutoPlayTurnId(null);},
  onError:message=>{setError(message);setMode('text');},
  onRecording:blob=>{const generation=version.current;void perform(async()=>{
   const active=current.current??await create();if(!active)return;
   const turnId=await api.voiceRecording(active,blob,crypto.randomUUID(),podcastContext);
   if(alive.current&&generation===version.current&&current.current===active){setAutoPlayTurnId(turnId);followLatest.current=true;setBehind(false);}
  });},
 });
 const recording=listening==='recording',preparing=listening==='permission'||listening==='loading';

 useEffect(()=>{
  if(autoPlayTurnId&&view?.turns.some(turn=>turn.turn_id===autoPlayTurnId&&['failed','cancelled'].includes(turn.status)))setAutoPlayTurnId(null);
 },[view,autoPlayTurnId]);
 useEffect(()=>{if(!busy&&!recording&&!preparing&&focusAfterAction.current){focusAfterAction.current=false;input.current?.focus();}},[busy,recording,preparing,mode]);
 const turns=view?.turns??[];
 const showOutgoing=outgoing!==null&&turns.length<=outgoing.after;
 useEffect(()=>{if(outgoing!==null&&!showOutgoing)setOutgoing(null);},[outgoing,showOutgoing]);
 const lastTurn=turns.at(-1);
 const toLatest=()=>{const element=transcript.current;if(element)element.scrollTop=element.scrollHeight;followLatest.current=true;setBehind(false);};
 useLayoutEffect(()=>{
  const element=input.current;if(element){element.style.height='auto';element.style.height=`${element.scrollHeight}px`;}
  if(followLatest.current&&transcript.current)transcript.current.scrollTop=transcript.current.scrollHeight;
 },[question,mode]);
 // 使用者往上捲閱讀時不強制拉回，只顯示「最新訊息」按鈕。
 useLayoutEffect(()=>{
  if(followLatest.current&&transcript.current)transcript.current.scrollTop=transcript.current.scrollHeight;
  else if(lastTurn)setBehind(true);
 },[id,lastTurn?.turn_id,lastTurn?.status,lastTurn?.answer?.text,lastTurn?.audio_url,lastTurn?.stage,lastTurn?.audio_sentences?.map(sentence=>sentence.status).join(','),showOutgoing]);
 const statusText=listening==='permission'?'等待麥克風權限…':listening==='loading'?'正在載入人聲辨識…':recording?'正在聆聽你的問題，說完稍停即可送出':busy?'正在送出…':working?(view?.turns.some(turn=>turn.status==='transcribing')?'正在辨識':'正在整理回答'):mode==='voice'?'正在聆聽':'Enter 送出 · Shift+Enter 換行';
 return <div className="voice-panel" onKeyDown={event=>{
  if(event.key==='Escape'&&historyOpen){event.stopPropagation();event.preventDefault();closeHistory(true);}
 }}>
  <header className="voice-header"><div className="voice-identity"><span className="voice-identity-icon" aria-hidden="true"><img src={mascot} alt=""/></span><div><h2>Studydy AI BOT</h2><small>只依據這份教材回答</small></div></div>
   <div className="voice-header-actions">
    <button ref={historyOpener} className="voice-icon-button" type="button" aria-label="對話紀錄" title="對話紀錄" aria-expanded={historyOpen} aria-controls="voice-history" onClick={()=>historyOpen?closeHistory(false):setHistoryOpen(true)}><Icon name="clock" size={18}/></button>
    <button className="voice-icon-button" type="button" aria-label="新對話" title="新對話" disabled={busy||recording||preparing} onClick={()=>choose(null)}><Icon name="plus" size={18}/></button>
    <button className="voice-icon-button" type="button" aria-label="關閉" title="關閉" onClick={onClose}><Icon name="close" size={18}/></button>
   </div>
  </header>
  {view&&!view.is_current_revision&&<p className="voice-revision-note">使用先前的教材版本。開啟新對話可使用更新後內容。</p>}
  <div className="voice-body">
   <div className="voice-turns" ref={transcript} aria-live="polite" aria-relevant="additions text" tabIndex={0} aria-label="Studydy AI BOT 紀錄" onScroll={event=>{
    const element=event.currentTarget;followLatest.current=element.scrollHeight-element.clientHeight-element.scrollTop<48;
    if(followLatest.current)setBehind(false);
   }}>
    {(!id||view?.turns.length===0)&&!showOutgoing&&<div className="voice-empty"><img className="voice-empty-mascot" src={mascot} alt=""/><h3>教材裡哪個地方想再了解？</h3><p>{mode==='voice'?'正在聆聽。說完稍停，會自動送出；語音製作中暫停收音，完成後可在播放時開口打斷。':'輸入問題，回答會附上教材來源頁碼。'}</p>
     {mode==='text'&&suggestions.length>0&&<div className="voice-suggestions" role="group" aria-label="建議問題">{suggestions.map(text=><button type="button" className="voice-suggestion" key={text} onClick={()=>{setQuestion(text);input.current?.focus();}}>{text}</button>)}</div>}
    </div>}
    {id&&!view&&!showOutgoing&&<p className="voice-inline-note" role="status">正在讀取對話…</p>}
    {turns.map(turn=>{
     const citations=turn.answer?.schema==='material-answer/v3'
       ? turn.answer.citations.map(citation=>({...citation,evidence:answerSources({...turn.answer!,citations:[citation]}).sources}))
       : answerSources(turn.answer).sources.map(evidence=>({passage_id:evidence.evidence_id,text:evidence.quote,evidence:[evidence]}));
     if(turn.status==='cancelled'&&!turn.answer)return <article className="voice-turn voice-cancelled" key={turn.turn_id}><small>已取消這次提問</small></article>;
     const waiting=pending.has(turn.status);
     const waitingLabel=(waiting?stageLabels[turn.stage??'']:undefined)??labels[turn.status]??'正在處理…';
     return <article key={turn.turn_id} className="voice-turn">
      <div className="voice-question" aria-label="你的提問">{turn.context&&<small>Podcast 第 {turn.context.episode_index+1} 集 · 所選段落</small>}<p>{turn.question||'語音提問'}</p>{turn.answer?.question_interpretation&&<small>依對話理解為：{turn.answer.question_interpretation.used}</small>}</div>
      <div className="voice-reply"><img className="voice-avatar" src={mascot} alt=""/>
       <div className="voice-response" aria-label="教材回答">
        {turn.answer&&<>
         {turn.answer.schema==='material-answer/v3' && turn.answer.paragraphs ? turn.answer.paragraphs.map((paragraph,index)=><p key={index} className="voice-answer"><MathText text={paragraph.text}/>{paragraph.citation_ids.map(id=>{const number=citations.findIndex(c=>c.passage_id===id)+1;const citation=citations[number-1];return citation?.evidence.map(evidence=><SourceButton key={`${id}:${evidence.evidence_id}`} apiClient={api} resolver={view!.source_resolver} evidence={evidence} reference={{number,quote:evidence.quote}}/>);})}</p>) : <p className="voice-answer"><MathText text={turn.answer.text}/></p>}
         <div className="voice-reference-sources" aria-label="回答來源"><strong>來源</strong>{citations.map((citation,index)=><div key={citation.passage_id}>{citation.evidence.map(evidence=><SourceButton key={evidence.evidence_id} apiClient={api} resolver={view!.source_resolver} evidence={evidence} reference={{number:index+1,list:true,quote:evidence.quote}}/>)}</div>)}{!citations.length&&<small>教材沒有足夠依據。</small>}</div>
         {(turn.audio_url||!!turn.audio_sentences?.length)&&<div className="voice-answer-actions"><VoiceAnswerAudio src={turn.audio_url} sentences={turn.audio_sentences} status={turn.status} activeAudio={activeAnswer} autoPlay={mode==='voice'&&autoPlayTurnId===turn.turn_id} onAutoPlayAttempt={()=>setAutoPlayTurnId(previous=>previous===turn.turn_id?null:previous)} register={element=>audios.current.add(element)} onPlay={element=>pauseMedia(element)}/></div>}
        </>}
        {waiting&&!turn.answer&&<p className="voice-typing"><span aria-hidden="true"><i/><i/><i/></span>{waitingLabel}</p>}
       </div>
      </div>
      {turn.status!=='ready'&&<div className="voice-turn-status">{(!waiting||turn.answer)&&<small>{turn.status==='failed'&&turn.answer?'文字回答已完成':waitingLabel}</small>}
       {waiting&&<button className="text-button" disabled={busy} onClick={()=>turnAction(turn,'cancel')}>取消這次提問</button>}
      </div>}
      {turn.status==='failed'&&<div className="voice-turn-status is-failed"><p className="voice-inline-note">{turn.answer?'語音製作未完成，文字與來源已保留。':'目前無法完成處理。'}{turn.error_code==='VOICE_TRANSCRIPT_INVALID'?'沒有辨識到清楚語音，請重新錄音。':turn.error_code==='VOICE_PODCAST_CONTEXT_INVALID'||turn.error_code==='RESOURCE_NOT_FOUND'?'原 Podcast 已不可用，請直接向 Studydy AI BOT 提問。':''}</p>{turn.question&&<button className="text-button" disabled={busy||working} onClick={()=>turnAction(turn,'retry')}>重試{turn.answer?'語音':'回答'}</button>}</div>}
     </article>;
    })}
    {showOutgoing&&<article className="voice-turn is-sending"><div className="voice-question" aria-label="你的提問"><p>{outgoing?.text}</p></div><div className="voice-reply"><img className="voice-avatar" src={mascot} alt=""/><div className="voice-response"><p className="voice-typing"><span aria-hidden="true"><i/><i/><i/></span>正在送出問題…</p></div></div></article>}
   </div>
   {behind&&!historyOpen&&<button type="button" className="voice-latest" onClick={toLatest}><Icon name="chevron-right" size={14}/>最新訊息</button>}
   {historyOpen&&<div ref={historyPanel} id="voice-history" className="voice-history" role="group" aria-label="已保存的對話">
    <div className="voice-history-head"><h3>對話紀錄</h3><button type="button" className="voice-history-new" disabled={busy||recording||preparing} onClick={()=>choose(null)}><Icon name="plus" size={16}/>新對話</button></div>
    <div className="voice-history-list">{list.map(c=><button type="button" key={c.conversation_id} data-conversation-id={c.conversation_id} title={c.title} aria-current={c.conversation_id===id?'true':undefined} disabled={busy} onClick={()=>{switchMode('text');choose(c.conversation_id);}}><Icon name="chat" size={16}/><span>{c.title}</span></button>)}{!list.length&&<p>還沒有對話。問第一個問題後會出現在這裡。</p>}</div>
    {id&&(confirmingDelete
     ?<InlineConfirm message="刪除這段對話及語音？教材會保留。" confirmLabel="刪除對話" busyLabel="正在刪除…" busy={busy}
       onCancel={()=>setConfirmingDelete(false)} onConfirm={()=>void perform(async()=>{await api.studyTools(`/v1/voice-conversations/${id}`,undefined,undefined,'DELETE');if(alive.current){setConfirmingDelete(false);choose(null);await refreshList();}})}/>
     :<button type="button" className="text-button voice-delete" data-inline-confirm-trigger disabled={busy||recording||preparing} onClick={()=>setConfirmingDelete(true)}><Icon name="trash" size={16}/>刪除對話</button>)}
   </div>}
  </div>
  <form className="voice-composer" onSubmit={event=>{event.preventDefault();if(!busy&&!working&&!recording&&!preparing)send();}}>
   {error&&<p role="alert" className="form-error">{error}</p>}
   <div className={`voice-composer-row${mode==='voice'?' is-voice':''}${recording?' is-recording':''}`}>
    <button type="button" className="voice-mic" role="switch" aria-label="語音模式" title={mode==='voice'?'關閉語音模式':'開啟語音模式'} aria-checked={mode==='voice'} onPointerEnter={prefetchVoiceAssets} onFocus={prefetchVoiceAssets} onClick={()=>switchMode(mode==='voice'?'text':'voice')}><Icon name="microphone" size={18}/></button>
    <textarea ref={input} className="voice-composer-input" aria-label="問題／辨識文字" rows={1} maxLength={4000} value={question} disabled={busy||recording||preparing} onChange={event=>setQuestion(event.target.value)} placeholder={mode==='voice'?'直接說話，或輸入問題':'問問這份教材…'} title="Enter 送出，Shift+Enter 換行" onKeyDown={event=>{
     if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing&&event.keyCode!==229){event.preventDefault();if(!busy&&!working&&!recording&&!preparing)send();}
    }}/>
    <button className="voice-send" type="submit" aria-label="送出問題" title="送出問題" disabled={busy||working||recording||preparing||!question.trim()}><Icon name={busy?'process':'send'} size={18}/></button>
   </div>
   <div className="voice-composer-status" role="status">{(recording||mode==='voice')&&<span className="voice-live-dot" aria-hidden="true"/>}<span>{statusText}</span></div>
  </form>
 </div>;
}

import { useEffect, useRef, useState } from 'react';
import { errorMessage, type StudydyApiClient } from '../../api/client';
import type { PodcastView } from '../../api/contracts';
import { writeRoute } from '../../app/routes';

export function PodcastAssessment({api,view,ended,onLeave}:{api:StudydyApiClient;view:PodcastView;ended:boolean;onLeave:()=>void}) {
  const concepts=[...new Map(view.episodes.flatMap(e=>e.claims.map(c=>[c.concept_id,c.label] as const))).entries()];
  const [concept,setConcept]=useState(concepts[0]?.[0]??'');
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const keys=useRef(new Map<string,string>()),alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[]);
  const enter=async()=>{
    if(busy||!view.run_id)return;
    setBusy(true);setError('');
    try {
      if(!keys.current.has(concept))keys.current.set(concept,crypto.randomUUID());
      const session=await api.createStudySession({schema:'study-session-create/v1',material_id:view.material_id,
        knowledge_structure_revision:view.knowledge_structure_revision,current_concept_id:concept},keys.current.get(concept));
      if(!alive.current)return;
      let selectedSet:string|undefined;
      if(session.status==='completed') {
        const history=await api.listAssessmentSets(session.study_session_id);
        if(!alive.current)return;
        if(history.knowledge_structure_revision!==view.knowledge_structure_revision){setError('檢測版本不一致。');return;}
        selectedSet=history.sets.find(s=>s.target_concept_id===concept)?.set_id;
        if(!selectedSet){setError('此版本已完成，所選概念沒有可開啟的既有題組，請從原版本知識地圖查看結果。');return;}
      } else if(session.current_concept_id!==concept)await api.focusStudySession(session.study_session_id,concept);
      if(!alive.current)return;
      onLeave();writeRoute({name:'study-session',materialId:view.material_id,runId:view.run_id,
        structureRevision:view.knowledge_structure_revision,studySessionId:session.study_session_id,...(selectedSet?{assessmentSetId:selectedSet}:{})});
    }catch(e){if(alive.current)setError(errorMessage(e))}finally{if(alive.current)setBusy(false)}
  };
  return <section className="surface podcast-assessment" aria-label="Podcast 檢測">
    <strong>{ended?'已播放至末集結尾，接著檢測理解':'檢測 Podcast 涵蓋的概念'}</strong>
    <p>使用這份 Podcast 建立時的教材版本。播放不會更新掌握度，測驗作答才會。</p>
    <label>選擇檢測概念 <select value={concept} disabled={busy} onChange={e=>setConcept(e.target.value)}>{concepts.map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
    <button className="secondary-button" disabled={busy||!concept||!view.run_id} onClick={()=>void enter()}>{busy?'正在開啟檢測…':'進入既有測驗'}</button>
    {error&&<><p role="alert" className="form-error">{error}</p>{view.run_id&&<button className="text-button" onClick={()=>{onLeave();writeRoute({name:'knowledge-map',materialId:view.material_id,runId:view.run_id!,structureRevision:view.knowledge_structure_revision})}}>查看原版本知識地圖</button>}</>}
  </section>;
}

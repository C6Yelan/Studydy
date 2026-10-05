"""多來源 beat、逐字 cue 與來源位置保持一致。"""
from copy import deepcopy
import pytest
from runtime.podcast_script import SCHEMA, validate, references, sources, digest
from runtime.podcast_video_plan import timeline_for_video, validate_plan
from runtime.podcast_timeline import webvtt
from runtime.scene_alignment import normalized


def sample():
    texts=['第一個來源要求保留必要條件。','第二個來源提供不同的比較條件。']
    claims=[{'concept_id':f'concept-{i}','claim_id':'same-id','label':f'來源{i}','text':t,
             'evidence':[{'evidence_id':f'e{i}','page_ref':f'p{i}','page':i+1,'quote':t}]} for i,t in enumerate(texts)]
    parts=[{'text':t,'source_refs':[{'source_index':i,'evidence_ids':[f'e{i}']}]} for i,t in enumerate(texts)]
    script={'schema':SCHEMA,'provider':'synthetic','segments':[{'beat_id':'beat-0','title':'比較兩個必要條件',
        'turns':[{'speaker':'host','text':''.join(texts),'parts':parts}]}],
        'review':{k:{'passed':True,'reason':'fixture'} for k in ('correctness','teaching_quality')}}
    episode={'delivery':'solo','claims':claims,'script':script,'audio':{'sha256':'a'*64,'duration_seconds':4}}
    cues=[{'title':f'条件{i}','parts':[{'turn_index':0,'text':t}]} for i,t in enumerate(texts)]
    anchors=[{'script_offset':0,'text':normalized(t)[:10],'audio_start':i*2,
        'boundary_script_offset':0,'boundary_text':normalized(t)[:10],'boundary_audio_start':i*2} for i,t in enumerate(texts)]
    alignment={'starts':[0,2],'anchors':anchors,'duration':4,'method':'synthetic','producer':'fixture'}
    return episode,cues,alignment


def test_multiple_sources_one_beat_and_precise_cue_evidence():
    episode,cues,alignment=sample();before=digest(episode['script'])
    assert validate(episode['script'],episode)==episode['script']
    timeline=timeline_for_video('p',0,episode,cues,alignment,'/exact/revision/evidence')
    assert [[e['evidence_id'] for e in cue['evidence']] for cue in timeline['segments']]==[['e0'],['e1']]
    assert [cue['source_bindings'][0]['source_index'] for cue in timeline['segments']]==[0,1]
    assert all(c['beat_ids']==['beat-0'] for c in timeline['segments'])
    assert ''.join(c['text'] for c in timeline['segments'])==episode['script']['segments'][0]['turns'][0]['text']
    assert all(t in webvtt(timeline) for t in [c['text'] for c in timeline['segments']])
    assert digest(episode['script'])==before


@pytest.mark.parametrize('change',['missing','foreign','text','review','duplicate','boolean_index'])
def test_rejects_invalid_source_contract(change):
    episode,_,_=sample();script=episode['script'];ref=script['segments'][0]['turns'][0]['parts'][0]['source_refs'][0]
    if change=='missing':script['segments'][0]['turns'][0]['parts'][1]['source_refs']=[]
    elif change=='foreign':ref['evidence_ids']=['e1']
    elif change=='text':script['segments'][0]['turns'][0]['text']+='新增文字'
    elif change=='review':script['review']['teaching_quality']['passed']=False
    elif change=='duplicate':script['segments'][0]['turns'][0]['parts'][0]['source_refs'].append(deepcopy(ref))
    else:ref['source_index']=False
    with pytest.raises(ValueError,match='PODCAST_SCRIPT_INVALID'):validate(script,episode)


def test_claim_can_span_beats_and_narrative_has_no_fabricated_evidence():
    episode,_,_=sample();script=episode['script'];second=deepcopy(script['segments'][0]);second['beat_id']='beat-1'
    script['segments'].append(second)
    narrative={'text':'接著想想看。','source_refs':[]}
    second['turns']=[{'speaker':'host','text':narrative['text'],'parts':[narrative]}]
    validate(script,episode)
    assert references(episode,1)==[] and sources(episode,[])==([],[])


def test_partial_part_and_cross_part_range():
    episode,_,_=sample();split=len(episode['script']['segments'][0]['turns'][0]['parts'][0]['text'])
    assert [r['source_index'] for r in references(episode,0,0,split,split+1)]==[1]
    assert [r['source_index'] for r in references(episode,0,0,split-1,split+1)]==[0,1]


def test_legacy_projection_never_mutates_saved_script():
    episode,_,_=sample();episode['script']={'provider':'old','segments':[{'claim_id':c['claim_id'],'turns':[{'speaker':'host','text':c['text']}]} for c in episode['claims']]}
    before=deepcopy(episode)
    assert references(episode,1)==[{'source_index':1,'evidence_ids':['e1']}]
    assert episode==before


def test_legacy_source_mismatch_remains_blocking():
    episode,_,_=sample();episode['script']={'provider':'old','segments':[{'claim_id':'wrong','turns':[{'speaker':'host','text':'錯誤對應'}]}]}
    with pytest.raises(ValueError,match='VIDEO_SOURCE_CHANGED'):references(episode,0)


def test_free_beats_keep_the_existing_episode_text_budget():
    episode,_,_=sample();base=episode['script']['segments'][0]
    for part in base['turns'][0]['parts']:part['text']='字'*800
    base['turns'][0]['text']='字'*1600
    episode['script']['segments']=[{**deepcopy(base),'beat_id':f'beat-{i}'} for i in range(6)]
    validate(episode['script'],episode)
    episode['script']['segments'].append({**deepcopy(base),'beat_id':'beat-6'})
    with pytest.raises(ValueError,match='PODCAST_SCRIPT_INVALID'):validate(episode['script'],episode)

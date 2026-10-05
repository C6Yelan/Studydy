from copy import deepcopy
import pytest
from runtime.podcast_video_plan import validate_cues, validate_plan, timeline_for_video


def sample():
    episode={'delivery':'dialogue','audio':{'sha256':'audio','duration_seconds':4},'claims':[
        {'claim_id':'a','evidence':[{'evidence_id':'ea','page_ref':1}]},
        {'claim_id':'b','evidence':[{'evidence_id':'eb','page_ref':2}]}],
        'script':{'provider':'synthetic','segments':[
            {'claim_id':'a','turns':[{'speaker':'host','text':'這是第一個有來源的觀念。'},{'speaker':'guest','text':'對。'}]},
            {'claim_id':'b','turns':[{'speaker':'guest','text':'第二個觀念需要明確來源。'}]}]}}
    cues=[{'title':'第一點','parts':[{'turn_index':0,'text':'這是第一個有來源的觀念。'}]},
          {'title':'第二點','parts':[{'turn_index':1,'text':'對。'},{'turn_index':2,'text':'第二個觀念需要明確來源。'}]}]
    texts=validate_cues(episode,cues)
    anchors=[]
    for i,text in enumerate(texts):
        quote=''.join(c for c in text if c.isalnum())
        anchors.append({'script_offset':0,'text':quote,'audio_start':i*2,
                        'boundary_script_offset':0,'boundary_text':quote,'boundary_audio_start':i*2})
    alignment={'starts':[0,2],'anchors':anchors,'duration':4,'method':'synthetic','producer':'synthetic'}
    return episode,cues,alignment


def plan():
    return {'pages':[{'title':'兩個觀念','start_cue':0,'end_cue':1,'elements':[
        {'kind':'box','cue_index':0,'text':'第一點','x':100,'y':250,'w':600,'h':200,'size':42,'color':'teal','filled':False},
        {'kind':'box','cue_index':1,'text':'第二點','x':1000,'y':250,'w':600,'h':200,'size':42,'color':'blue','filled':False}]}]}


def test_short_turn_can_share_a_cue_without_losing_speakers_or_sources():
    episode,cues,alignment=sample()
    result=timeline_for_video('p',0,episode,cues,alignment,'/source')
    second=result['segments'][1]
    assert second['turns']==[{'speaker':'guest','text':'對。'},{'speaker':'guest','text':'第二個觀念需要明確來源。'}]
    assert second['claim_ids']==['a','b']
    assert [e['page_ref'] for e in second['evidence']]==[1,2]
    assert second['start']==2 and second['end']==4


@pytest.mark.parametrize('change',['rewrite','omit','reorder'])
def test_model_cannot_change_or_skip_the_original_script(change):
    episode,cues,_=sample()
    if change=='rewrite':cues[0]['parts'][0]['text']='模型改寫的句子。'
    if change=='omit':cues[1]['parts'].pop(0)
    if change=='reorder':cues.reverse()
    with pytest.raises(ValueError,match='VIDEO_TRANSCRIPT_INVALID'):validate_cues(episode,cues)


@pytest.mark.parametrize('change',['offscreen','markup','future_cue','overlap_pages','extra_key'])
def test_plan_rejects_unsafe_shapes_and_wrong_page_ranges(change):
    _,cues,_=sample();value=plan()
    if change=='offscreen':value['pages'][0]['elements'][0]['x']=1900
    if change=='markup':value['pages'][0]['elements'][0]['kind']='svg'
    if change=='future_cue':value['pages'][0]['elements'][0]['cue_index']=2
    if change=='overlap_pages':value['pages'].append(deepcopy(value['pages'][0]))
    if change=='extra_key':value['pages'][0]['elements'][0]['html']='untrusted'
    with pytest.raises(ValueError,match='VIDEO_STORYBOARD_INVALID'):validate_plan(value,cues)


def test_rejects_estimated_time_that_disagrees_with_word_anchor():
    episode,cues,alignment=sample();alignment['starts'][1]=1.5
    with pytest.raises(ValueError,match='VIDEO_ALIGNMENT_INVALID'):
        timeline_for_video('p',0,episode,cues,alignment,'/source')


def test_long_dialogue_preserves_all_turns_sources_and_final_boundary():
    episode={'audio':{'sha256':'synthetic','duration_seconds':96},'claims':[],'script':{'provider':'synthetic','segments':[]}}
    cues=[];anchors=[]
    for segment in range(6):
        claim_id=f'claim-{segment}'
        episode['claims'].append({'claim_id':claim_id,'evidence':[{'evidence_id':f'ev-{segment}','page_ref':segment+1}]})
        turns=[]
        for turn in range(8):
            text=f'第{segment+1}段第{turn+1}輪，保留大小寫 bit/s 與 MB，也保留必要條件。'
            turns.append({'speaker':'host' if turn%2==0 else 'guest','text':text})
            index=len(cues);cues.append({'title':f'觀念{segment+1}','parts':[{'turn_index':index,'text':text}]})
            from runtime.scene_alignment import normalized
            quote=normalized(text)
            anchors.append({'script_offset':0,'text':quote,'audio_start':index*2,'boundary_script_offset':0,'boundary_text':quote,'boundary_audio_start':index*2})
        episode['script']['segments'].append({'claim_id':claim_id,'turns':turns})
    alignment={'starts':list(range(0,96,2)),'duration':96,'method':'synthetic','producer':'synthetic','anchors':anchors}
    result=timeline_for_video('synthetic',0,episode,cues,alignment,'/source')
    assert len(result['segments'])==48 and result['segments'][-1]['end']==96
    assert [turn for cue in result['segments'] for turn in cue['turns']]==[turn for s in episode['script']['segments'] for turn in s['turns']]
    for index,cue in enumerate(result['segments']):
        assert cue['claim_ids']==[f'claim-{index//8}']
        assert cue['evidence'][0]['page_ref']==index//8+1


def test_short_question_and_answer_keep_distinct_speakers_in_one_cue():
    episode,cues,alignment=sample()
    episode['script']['segments'][0]['turns'][1]['speaker']='host'
    result=timeline_for_video('synthetic',0,episode,cues,alignment,'/source')
    assert [t['speaker'] for t in result['segments'][1]['turns']]==['host','guest']


def test_invalid_geometry_reports_the_element_and_allowed_bounds():
    _,cues,_=sample();value=plan()
    value['pages'][0]['elements'][1]['y']=750
    with pytest.raises(ValueError,match=r'page=0,element=1.*bounds.*y=220\.\.810'):
        validate_plan(value,cues)


def marked_plan():
    value=plan()
    value['pages'][0]['emphasis']=[{'element_index':0,'start_cue':0,'end_cue':0,'kind':'underline','quote':'第一點'}]
    return value


@pytest.mark.parametrize('failure',['future','outside_page','missing_target','missing_quote','ambiguous_quote','trace_text','duplicate'])
def test_temporary_marks_cannot_invent_text_or_outlive_their_source(failure):
    _,cues,_=sample();value=marked_plan();page=value['pages'][0];mark=page['emphasis'][0]
    if failure=='future':mark['element_index']=1;mark['quote']='第二點'
    if failure=='outside_page':mark['end_cue']=2
    if failure=='missing_target':mark['element_index']=9
    if failure=='missing_quote':mark['quote']='來源沒有的文字'
    if failure=='ambiguous_quote':page['elements'][0]['text']='第一點與第一點'
    if failure=='trace_text':mark['kind']='trace';mark['quote']=''
    if failure=='duplicate':page['emphasis'].append(deepcopy(mark))
    with pytest.raises(ValueError,match='VIDEO_STORYBOARD_INVALID'):validate_plan(value,cues)


def test_marks_can_end_before_the_page_and_old_static_plans_still_load():
    _,cues,_=sample();value=marked_plan()
    assert validate_plan(value,cues)['pages'][0]['emphasis'][0]['end_cue']==0
    assert validate_plan(plan(),cues)==plan()


def test_three_distinct_phrases_can_be_compared_simultaneously():
    _,cues,_=sample();value=marked_plan();page=value['pages'][0]
    page['elements'][0]['text']='第一點、第二點、第三點'
    page['emphasis'] += [{**page['emphasis'][0],'quote':quote} for quote in ('第二點','第三點')]
    assert len(validate_plan(value,cues)['pages'][0]['emphasis'])==3


def test_asr_grouping_preserves_original_roles_and_all_source_parts():
    from runtime.podcast_video_plan import merge_cue_groups
    episode,cues,_=sample();merged=merge_cue_groups(episode,cues,[[0,1]])
    assert len(merged)==1
    assert merged[0]['parts']==cues[0]['parts']+cues[1]['parts']
    validate_cues(episode,merged)
    with pytest.raises(ValueError,match='VIDEO_ALIGNMENT_INVALID'):merge_cue_groups(episode,cues,[[1],[0]])


def test_early_mark_feedback_names_the_explanation_range_despite_full_page_visibility():
    _,cues,_=sample();value=marked_plan();mark=value['pages'][0]['emphasis'][0]
    mark.update(element_index=1,quote='第二點')
    with pytest.raises(ValueError,match=r'requested cues=0\.\.0.*emphasized.*cues=1\.\.1'):
        validate_plan(value,cues)


@pytest.mark.parametrize('change',['duplicate','late','before_page','after_page','foreign','too_many'])
def test_progressive_reveal_contract(change):
    episode,cues,_=sample();value=plan();page=value['pages'][0]
    page['emphasis']=[];page['reveal']=[{'start_cue':1,'elements':[1]}]
    page['elements'][1]['cue_index']=1
    if change=='duplicate':page['reveal'][0]['elements']=[1,1]
    elif change=='late':page['elements'][1]['cue_index']=0
    elif change=='before_page':page['reveal'][0]['start_cue']=-1
    elif change=='after_page':page['reveal'][0]['start_cue']=2
    elif change=='foreign':page['reveal'][0]['elements']=[60]
    else:page['reveal']*=4
    with pytest.raises(ValueError,match='VIDEO_STORYBOARD_INVALID'):validate_plan(value,cues)


def test_reveal_cannot_show_connection_before_target_node():
    _,cues,_=sample();value=plan();page=value['pages'][0]
    # 一條可定位的連線，目標節點在後一個 cue 才出現。
    page['elements']=[{'kind':'box','cue_index':0,'text':'起點','x':100,'y':300,'w':300,'h':200,'size':42,'color':'teal','filled':True},
        {'kind':'box','cue_index':1,'text':'終點','x':900,'y':300,'w':300,'h':200,'size':42,'color':'blue','filled':True},
        {'kind':'arrow','cue_index':1,'text':'','x':400,'y':400,'w':500,'h':0,'size':28,'color':'ink','filled':False}]
    page['emphasis']=[];page['reveal']=[{'start_cue':1,'elements':[1]}]
    with pytest.raises(ValueError,match='connection must not precede'):validate_plan(value,cues)
    page['reveal'][0]['elements'].append(2)
    assert validate_plan(value,cues)


def test_emphasis_generation_schema_separates_trace_and_single_line_quotes():
    from runtime.podcast_video_plan import emphasis_schema
    choices=emphasis_schema([{},{}])['items']['anyOf']
    by_kind={x['properties']['kind']['enum'][0]:x['properties']['quote'] for x in choices}
    assert by_kind['trace']['enum']==['']
    assert by_kind['underline']['minLength']==1 and by_kind['outline']['minLength']==0
    assert by_kind['outline']['pattern']==r'^[^\r\n]*$'


def test_multiline_quote_feedback_is_not_misreported_as_duplicate_text():
    _,cues,_=sample();value=plan();page=value['pages'][0];page['elements'][0]['text']='第一行\n第二行'
    page['emphasis']=[{'element_index':0,'start_cue':0,'end_cue':0,'kind':'outline','quote':'第一行\n第二行'}]
    with pytest.raises(ValueError,match='quote must be single-line'):validate_plan(value,cues)

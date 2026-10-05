"""固定詞時間點驗證對齊規則；不是實際 ASR 品質測試。"""
import pytest
from runtime.scene_alignment import align_texts,normalized
from runtime.podcast_scenes import flow_steps


def test_measured_word_times_define_scene_boundaries_not_text_lengths():
    texts=['第一段說明可靠傳輸的重要概念。','第二段說明封包傳送的完整順序。']
    words=[{'word':texts[0],'start':0.4,'end':1.7},{'word':texts[1],'start':7.3,'end':9.2}]
    result=align_texts(texts,words,10)
    assert result['starts']==[0,7.3]
    assert result['anchors'][1]['text'] in normalized(texts[1])
    assert result['method']=='whisper-phonetic-boundaries/v2'


def test_unmatched_or_reversed_speech_does_not_get_invented_timing():
    texts=['第一段說明可靠傳輸的重要概念。','第二段說明封包傳送的完整順序。']
    with pytest.raises(ValueError,match='SCENE_ALIGNMENT_FAILED'):
        align_texts(texts,[{'word':'與講稿完全不同的內容','start':0,'end':2}],10)
    with pytest.raises(ValueError,match='SCENE_ALIGNMENT_FAILED'):
        align_texts(texts,[{'word':texts[1],'start':1,'end':3},{'word':texts[0],'start':5,'end':8}],10)


def test_flow_requires_explicit_sequence_and_keeps_words():
    assert flow_steps({'text':'建立過程包含三個步驟：1. 用戶端傳送 SYN 旗標；2. 伺服器回覆 SYN + ACK 旗標；3. 用戶端傳送 ACK 旗標'})==[
        '用戶端傳送 SYN 旗標','伺服器回覆 SYN + ACK 旗標','用戶端傳送 ACK 旗標']
    assert flow_steps({'text':'理解 B 的先備知識是 A。'})==[]
    assert flow_steps({'text':'此過程比較 1.25 和 2.50 的差異。'})==[]
    assert flow_steps({'text':'流程：1. 如果失敗則停止；2. 否則繼續'})==[]


def test_homophones_align_without_rewriting_the_script():
    texts=['先確認兩端的連接埠與完整資料。','第二段接著說明資料傳送的步驟。']
    words=[{'word':'先確認兩端的連接部與完整資料','start':.2,'end':4},{'word':texts[1],'start':6,'end':9}]
    result=align_texts(texts,words,10)
    assert result['starts']==[0,6]
    assert '連接埠' in result['anchors'][0]['text']
    assert texts[0]=='先確認兩端的連接埠與完整資料。'


def test_short_opening_precedes_the_long_identity_witness():
    texts=['第一段說明可靠傳輸的重要概念。','接著談資料，這裡完整說明確認封包的傳送順序。']
    words=[{'word':texts[0],'start':0.4,'end':3},
        {'word':'接著談','start':5,'end':6},{'word':'一些東西','start':6,'end':7},
        {'word':'這裡完整說明確認封包的傳送順序','start':8,'end':12}]
    result=align_texts(texts,words,13)
    assert result['starts']==[0,5]
    assert result['anchors'][1]['audio_start']==8
    assert result['anchors'][1]['boundary_script_offset']==0


def test_diagram_requires_source_check_and_unsupported_result_keeps_original_text():
    from types import SimpleNamespace
    from runtime import podcast_scenes as scenes
    from runtime.source_normalization import SourceError
    claim={'claim_id':'synthetic-claim','concept_id':'synthetic-concept','label':'傳送流程',
        'text':'傳送步驟：1. 用戶端送出請求；2. 伺服器回覆結果','evidence':[]}
    spoken='第一步由用戶端送出請求，第二步伺服器回覆結果。'
    episode={'claims':[claim],'script':{'segments':[{'claim_id':claim['claim_id'],'turns':[{'text':spoken}]}]},
        'audio':{'sha256':'a'*64,'duration_seconds':10}}
    document={'relations':[]};podcast=SimpleNamespace(knowledge_structure_revision='synthetic-revision',material_id='synthetic-material')
    alignment={'starts':[0],'anchors':[{'script_offset':0,'text':normalized(spoken)[:12],'audio_start':0,'boundary_script_offset':0,'boundary_text':normalized(spoken)[:12],'boundary_audio_start':0}],
        'duration':10,'method':'synthetic-test','producer':'synthetic-test'}
    with pytest.raises(SourceError,match='SCENE_SOURCE_CHECK_INVALID'):scenes.build_manifest(podcast,episode,document,alignment)
    content=scenes.scene_content(episode,document)
    denied=scenes.checked_verdicts(content,{'provider':'synthetic-test','items':[{'index':0,'supported':False,'reason':'fixture has no supporting evidence'}]})
    manifest=scenes.build_manifest(podcast,episode,document,alignment,denied)
    assert manifest['scenes'][0]['kind']=='concept' and manifest['scenes'][0]['text']==claim['text']
    allowed=scenes.checked_verdicts(content,{'provider':'synthetic-test','items':[{'index':0,'supported':True,'reason':'synthetic supported scenario'}]})
    assert scenes.build_manifest(podcast,episode,document,alignment,allowed)['scenes'][0]['steps']==['用戶端送出請求','伺服器回覆結果']


def test_fine_cuts_merge_using_word_times_without_weakening_anchor_length():
    from runtime.scene_alignment import align_cue_groups
    texts=['先確認。','這是完整且有語音依據的必要條件。']
    words=[{'word':texts[0],'start':.4,'end':1},{'word':'呃','start':1.1,'end':1.3},{'word':texts[1],'start':1.5,'end':5}]
    result=align_cue_groups(texts,words,6,[[0],[0]])
    assert result['groups']==[[0,1]] and result['starts']==[0]
    assert result['anchors'][0]['audio_start']==1.5
    assert result['anchors'][0]['boundary_audio_start']==.4
    assert len(result['anchors'][0]['text'])>=8


def test_fully_recognized_short_reply_can_share_the_next_measured_cue():
    from runtime.scene_alignment import align_cue_groups
    texts=['對。','這是完整而可回查的語音說明。']
    words=[{'word':texts[0],'start':.2,'end':.6},{'word':texts[1],'start':1,'end':5}]
    assert align_cue_groups(texts,words,6,[[0],[1]])['groups']==[[0,1]]


def test_shared_asr_word_time_does_not_create_fake_separate_boundaries():
    from runtime.scene_alignment import align_cue_groups
    texts=['第一個可回查的完整片段','第二個也有明確原文依據']
    result=align_cue_groups(texts,[{'word':''.join(texts),'start':.2,'end':8}],9,[[0],[0]])
    assert result['groups']==[[0,1]] and result['starts']==[0]


@pytest.mark.parametrize('failure',['missing_turn','reversed','invalid_times','too_long'])
def test_grouping_still_refuses_unreliable_speech_and_oversized_captions(failure):
    from runtime.scene_alignment import align_cue_groups
    texts=['第一段完整且正確的語音內容。','第二段具有明確來源的教學說明。']
    words=[{'word':texts[0],'start':.2,'end':4},{'word':texts[1],'start':5,'end':8}]
    ids=[[0],[0]]
    if failure=='missing_turn':words.pop();ids=[[0],[1]]
    if failure=='reversed':words[0]['word'],words[1]['word']=words[1]['word'],words[0]['word']
    if failure=='invalid_times':words.reverse()
    if failure=='too_long':
        texts=['對。','這段合成測試說明需要保留完整內容。'*12]
        words=[{'word':texts[0],'start':.2,'end':.6},{'word':texts[1],'start':1,'end':8}]
    with pytest.raises(ValueError,match='SCENE_ALIGNMENT_FAILED'):align_cue_groups(texts,words,9,ids)

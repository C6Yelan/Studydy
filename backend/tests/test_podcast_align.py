"""對齊已取得的詞時間點，不執行 ASR 模型。"""
from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
import pytest
from runtime.podcast_cues import captions_for_episode

spec=spec_from_file_location('podcast_align_worker',Path(__file__).resolve().parents[2]/'ops/podcast/align.py')
worker=module_from_spec(spec);spec.loader.exec_module(worker)


def test_short_caption_anchors_are_accepted_by_the_consumer():
    turns=['確認。','開始傳送資料。']
    words=[{'word':turns[0],'start':.4,'end':2},{'word':turns[1],'start':5,'end':8}]
    result=worker.align_words(['\n'.join(turns)],words,10,[[0,1]],turns)
    episode={'script':{'segments':[{'turns':[{'text':text} for text in turns]}]},'audio':{'duration_seconds':10}}
    captions=captions_for_episode(episode,result['caption_alignment'])
    assert [c['text'] for c in captions]==turns
    assert [c['start'] for c in captions]==[0,5]
    assert captions[-1]['end']==10


def test_unreliable_fine_caption_keeps_the_verified_paragraph_clock():
    turns=['第一段完整說明資料傳送的原理。','嗯。','最後核對完整的教材來源與必要條件。']
    texts=['\n'.join(turns)]
    words=[{'word':turns[0],'start':.4,'end':4},{'word':turns[2],'start':7,'end':12}]
    result=worker.align_words(texts,words,15,[[0,1,2]],turns)
    assert 'caption_alignment' not in result
    assert result['starts']==[0] and result['anchors'][0]['boundary_audio_start']==.4
    assert result['method'].endswith(';paragraph-captions/v1')
    assert texts==['\n'.join(turns)]


def test_missing_paragraph_alignment_still_fails_without_fabricating_times():
    with pytest.raises(ValueError,match='SCENE_ALIGNMENT_FAILED'):
        worker.align_words(['甲乙丙丁'],[],10,[[0]],['甲乙丙丁'])


def test_foreign_caption_text_is_not_a_reason_to_fall_back():
    with pytest.raises(ValueError,match='SCENE_ALIGNMENT_FAILED'):
        worker.align_words(['原本完整的教材說明'],[{'word':'原本完整的教材說明','start':1,'end':5}],10,[[0]],['另一份講稿'])


def test_short_windows_use_context_but_keep_each_core_timestamp_once():
    from types import SimpleNamespace
    calls=[]
    class Model:
        def transcribe(self,samples,**kwargs):
            calls.append((len(samples),kwargs))
            # 相鄰視窗都辨識到同一個詞；依實際核心區間只收一次。
            start,end=(9.2,9.8) if len(calls)==1 else (1.2,1.8)
            word=SimpleNamespace(word='完整詞',start=start,end=end)
            return [SimpleNamespace(words=[word])],None
    words=worker.short_window_words(Model(),[0]*(20*16000))
    assert words==[{'word':'完整詞','start':9.2,'end':9.8}]
    assert [length for length,_ in calls]==[12*16000,12*16000]
    assert all(not options['vad_filter'] and options['no_speech_threshold'] is None for _,options in calls)


@pytest.mark.parametrize('recognized,begin,expected',[
    ('至少補充三件事，保留來源。',4,4),
    ('保留來源。',7,8),
    ('完全無關的另一段聲音。',4,8),
    ('至少補充三件事，保留來源。',0,8),
])
def test_missing_caption_prefix_uses_only_new_measured_nonoverlapping_boundary(recognized,begin,expected):
    from types import SimpleNamespace
    from copy import deepcopy
    turns=['先提出問題。','至少補充三件事，保留來源。','接著確認版本。']
    words=[{'word':turns[0],'start':.2,'end':2},{'word':'保留來源。','start':8,'end':10},
           {'word':turns[2],'start':12,'end':14}]
    result=worker.align_words(turns,words,16,[[0],[1],[2]],turns)
    original=deepcopy(result)
    class Model:
        def transcribe(self,samples,**kwargs):
            assert kwargs['word_timestamps'] and not kwargs['vad_filter']
            return [SimpleNamespace(words=[SimpleNamespace(word=recognized,start=begin,end=begin+2)])],None
    worker.repair_caption_prefixes(Model(),[0]*(16*16000),result)
    assert result['caption_alignment']['alignment']['starts']==[0,expected,12]
    assert result['starts']==original['starts']  # 教學段落與影片時鐘不移動。
    assert result['caption_alignment']['cues']==original['caption_alignment']['cues']
    episode={'script':{'segments':[{'turns':[{'text':text} for text in turns]}]},'audio':{'duration_seconds':16}}
    assert [c['text'] for c in captions_for_episode(episode,result['caption_alignment'])]==turns

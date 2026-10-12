"""CPU 音訊工程驗證；合成訊號不代表真實 TTS 音質。"""
import importlib.util
import math
from pathlib import Path
import wave
from array import array
import pytest

spec=importlib.util.spec_from_file_location('audio_mastering',Path(__file__).resolve().parents[2]/'ops/podcast/audio_mastering.py')
audio=importlib.util.module_from_spec(spec);spec.loader.exec_module(audio)


def signal(seconds=1):
    return [math.sin(i*2*math.pi*330/24000)*.1 for i in range(round(seconds*24000))]


def test_trim_only_edges_preserves_internal_pause_and_speech_margin():
    speech=signal(.2);samples=[0.]*4800+speech+[0.]*7200+speech+[0.]*4800
    result=audio.trim_edges(samples)
    assert len(result)<len(samples)
    assert result[:960]==[0.]*960 and result[-960:]==[0.]*960
    assert result[960:960+len(speech)]==speech
    assert result[960+len(speech):960+len(speech)+7200]==[0.]*7200


@pytest.mark.parametrize('samples',[[],[0.]*2400,[float('nan')]*2400,[float('inf')]*2400])
def test_invalid_or_silent_chunk_is_not_published(samples):
    with pytest.raises(ValueError,match='PODCAST_AUDIO_INVALID'):audio.trim_edges(samples)


def test_pauses_follow_semantic_boundaries_without_stacking():
    assert audio.pause_seconds('TCP',language_join=True)==.08
    assert audio.pause_seconds('說明。')==.22
    assert audio.pause_seconds('問題？',next_speaker=True)==.22
    assert audio.pause_seconds('問題？',next_speaker=True,next_beat=True)==.32
    assert audio.pause_seconds('句號。',language_join=True)==.22
    assert audio.pause_seconds('，',language_join=True)==.12


def test_real_ffmpeg_two_pass_mastering_and_decoded_aac(tmp_path):
    import subprocess
    try:audio.ffmpeg()
    except ValueError:pytest.skip('CPU FFmpeg unavailable; run with renderer site-packages on PYTHONPATH')
    raw=tmp_path/'source.wav';output=tmp_path/'master.wav';aac=tmp_path/'encoded.m4a';decoded=tmp_path/'decoded.wav'
    values=[math.sin(i*2*math.pi*(220 if i<96000 else 440)/24000)*(.05+.02*math.sin(i*2*math.pi/12000)) for i in range(192000)]
    with wave.open(str(raw),'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(array('h',(round(x*32767) for x in values)).tobytes())
    result=audio.master(raw,output)
    assert result['policy']=='podcast-mastering/v1' and abs(result['integrated_lufs']+19)<=1 and result['true_peak_dbtp']<=-1.9
    with wave.open(str(output),'rb') as w:
        assert (w.getnchannels(),w.getsampwidth(),w.getframerate())==(1,2,24000)
        assert abs(w.getnframes()/24000-8)<.05
    for args in ([audio.ffmpeg(),'-y','-i',str(output),'-c:a','aac','-b:a','192k',str(aac)],
                 [audio.ffmpeg(),'-y','-i',str(aac),'-c:a','pcm_s16le',str(decoded)]):
        subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=30)
    encoded=audio._run(decoded,'loudnorm=I=-19:TP=-2:LRA=11:print_format=json')
    assert float(encoded['input_tp'])<=-1 and abs(float(encoded['input_i'])+19)<=1


def test_mastering_failure_does_not_return_success(tmp_path,monkeypatch):
    monkeypatch.setattr(audio,'_run',lambda *args:(_ for _ in ()).throw(ValueError('PODCAST_AUDIO_INVALID')))
    with pytest.raises(ValueError,match='PODCAST_AUDIO_INVALID'):audio.master(tmp_path/'in.wav',tmp_path/'out.wav')


def test_mastered_video_encodes_and_measures_final_audio(tmp_path):
    pytest.importorskip('PIL')
    pytest.importorskip('imageio_ffmpeg')
    from hashlib import sha256
    import base64
    from runtime.podcast_video_render import render
    from runtime.podcast_video_plan import POLICY
    from runtime.podcast_videos import validate_bundle
    from test_podcast_beats import sample
    from test_podcast_video_plan import plan
    raw=tmp_path/'source.wav';mastered=tmp_path/'audio.wav'
    with wave.open(str(raw),'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(array('h',(round(x*32767) for x in signal(4))).tobytes())
    metadata=audio.master(raw,mastered)
    episode,cues,alignment=sample();episode['audio'].update(sha256=sha256(mastered.read_bytes()).hexdigest(),mastering=metadata)
    storyboard={**plan(),'schema':'podcast-storyboard/v2'}
    request={'episode':episode,'podcast_id':'synthetic','episode_index':0,'source_resolver':'/exact/revision/evidence',
        'cues':cues,'alignment':alignment,'plan':storyboard}
    destination=tmp_path/'video.mp4';output=render(request,mastered,destination)
    assert output['fps']==60 and output['encoded_audio']['true_peak_dbtp']<=-1
    state={**request,'index':0}
    bundle={'policy':POLICY,'model':'synthetic','plan':storyboard,'cues':cues,'alignment':alignment,
        'review':{k:{'passed':True,'reason':'synthetic'} for k in ('correctness','teaching_quality')},
        'render':output,'video':base64.b64encode(destination.read_bytes()).decode()}
    data,manifest=validate_bundle(state,bundle)
    assert data==destination.read_bytes() and manifest['audio_sha256']==episode['audio']['sha256']
    bundle['render']['encoded_audio']['true_peak_dbtp']=0
    assert validate_bundle(state,bundle)[0]==destination.read_bytes()


def test_retained_edges_count_toward_pause_budget():
    first=[.2]*100+[0.]*960;second=[0.]*960+[.2]*100
    head,tail=audio.edge_silence(first);next_head,_=audio.edge_silence(second)
    padding=max(0,round(audio.pause_seconds('連接',language_join=True)*24000)-tail)
    actual=tail+max(0,padding-next_head)+next_head
    assert head==0 and tail==960 and actual==1920


def test_measured_loudness_is_recorded_without_a_quality_veto(tmp_path,monkeypatch):
    measured={'input_i':'-21','input_tp':'-0.8','input_lra':'3','input_thresh':'-31','target_offset':'0'}
    monkeypatch.setattr(audio,'_run',lambda *args:measured)
    result=audio.master(tmp_path/'in.wav',tmp_path/'out.wav')
    assert result['integrated_lufs']==-21 and result['true_peak_dbtp']==-.8

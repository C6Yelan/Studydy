"""取消須終止渲染並阻止後續模型請求；不使用真實教材或模型。"""
import importlib.util
from pathlib import Path
import sys
from threading import Event, Timer
from uuid import uuid4

import pytest

spec=importlib.util.spec_from_file_location('podcast_video_service',Path(__file__).resolve().parents[2]/'ops/podcast/video_service.py')
service=importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def test_cancel_before_start_never_calls_model():
    body={'job_id':str(uuid4()),'job_token':str(uuid4())}
    service.cancel(body)
    with pytest.raises(ValueError,match='VIDEO_CANCELLED'):
        service.produce(body,luna=lambda *_:pytest.fail('cancelled job called model'),model='synthetic',align=None,text_lock=None,asr_lock=None)
    assert service.job_key(body) not in service.JOBS


def test_cancel_running_renderer_stops_child():
    cancelled=Event()
    timer=Timer(.3,cancelled.set);timer.start()
    try:
        with pytest.raises(ValueError,match='VIDEO_CANCELLED'):
            service._process([sys.executable,'-c','import time; time.sleep(30)'],None,10,cancelled)
    finally:timer.cancel()


@pytest.mark.parametrize('recover',[True,False])
def test_source_feedback_is_rechecked_and_never_published_without_approval(tmp_path,monkeypatch,recover):
    import base64,json
    from hashlib import sha256
    from threading import Lock
    from test_podcast_video_plan import sample,plan
    episode,cues,alignment=sample();audio=b'synthetic audio';episode['audio']['sha256']=sha256(audio).hexdigest()
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON',sys.executable)
    monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR',str(tmp_path))
    monkeypatch.setattr(service,'ensure_space',lambda _:None)
    reviews=0;designs=[];rendered=[]
    def model(prompt,schema,**kwargs):
        nonlocal reviews
        if 'cues' in schema['properties']:return {'cues':cues}
        if 'pages' in schema['properties']:
            designs.append(prompt);return plan()
        reviews+=1
        return {'supported':recover and reviews==2,'reason':'relation is unsupported'}
    def process(args,env,timeout,cancelled):
        result=Path(args[5])
        if args[-1]=='--validate':result.write_text('{"valid":true}')
        else:
            rendered.append(True);result.write_text('{}');result.with_suffix('.mp4').write_bytes(b'synthetic video')
        return 0
    monkeypatch.setattr(service,'_process',process)
    body={'episode':episode,'audio':base64.b64encode(audio).decode(),'source_context':{},'podcast_id':'p','episode_index':0,'source_resolver':'/source'}
    kwargs=dict(luna=model,model='synthetic',align=lambda *a,**k:alignment,text_lock=Lock(),asr_lock=Lock(),cancelled=Event())
    if recover:
        result=service._produce(body,**kwargs)
        assert result['review']['supported'] and len(rendered)==1 and reviews==2
    else:
        with pytest.raises(ValueError,match='VIDEO_STORYBOARD_NEEDS_REVIEW'):service._produce(body,**kwargs)
        assert not rendered and reviews==3
    assert 'relation is unsupported' in designs[1]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('overflow_only',[False,True])
def test_layout_and_source_corrections_have_independent_bounded_budgets(tmp_path,monkeypatch,overflow_only):
    import base64,json
    from hashlib import sha256
    from threading import Lock
    from test_podcast_video_plan import sample,plan
    episode,cues,alignment=sample();audio=b'synthetic audio';episode['audio']['sha256']=sha256(audio).hexdigest()
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON',sys.executable)
    monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR',str(tmp_path))
    monkeypatch.setattr(service,'ensure_space',lambda _:None)
    plans=[];reviews=0;rendered=[]
    def model(prompt,schema,**kwargs):
        nonlocal reviews
        if 'cues' in schema['properties']:return {'cues':cues}
        if 'pages' in schema['properties']:
            plans.append(prompt);return plan()
        reviews+=1
        return {'supported':reviews==2,'reason':'remove unsupported relation'}
    def process(args,env,timeout,cancelled):
        result=Path(args[5])
        if args[-1]=='--validate':
            if len(plans)<=2 or overflow_only:
                result.write_text('{"error":"VIDEO_LAYOUT_INVALID:page=0,element=0,height>=188"}');return 1
            result.write_text('{"valid":true}')
        else:
            rendered.append(True);result.write_text('{}');result.with_suffix('.mp4').write_bytes(b'synthetic video')
        return 0
    monkeypatch.setattr(service,'_process',process)
    body={'episode':episode,'audio':base64.b64encode(audio).decode(),'source_context':{},'podcast_id':'p','episode_index':0,'source_resolver':'/source'}
    kwargs=dict(luna=model,model='synthetic',align=lambda *a,**k:alignment,text_lock=Lock(),asr_lock=Lock(),cancelled=Event())
    if overflow_only:
        with pytest.raises(ValueError,match='VIDEO_LAYOUT_INVALID'):service._produce(body,**kwargs)
        assert len(plans)==3 and reviews==0 and not rendered
    else:
        result=service._produce(body,**kwargs)
        assert result['review']['supported'] and reviews==2 and len(plans)==4 and len(rendered)==1
        assert 'height>=188' in plans[1] and 'remove unsupported relation' in plans[3]


def test_measured_grouping_reaches_storyboard_and_render_without_dropping_turns(tmp_path,monkeypatch):
    import base64,json
    from hashlib import sha256
    from threading import Lock
    from test_podcast_video_plan import sample,plan
    episode,cues,alignment=sample();raw=b'synthetic';episode['audio']['sha256']=sha256(raw).hexdigest()
    alignment.update(groups=[[0,1]],starts=[0],anchors=[alignment['anchors'][0]])
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON',sys.executable);monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR',str(tmp_path));monkeypatch.setattr(service,'ensure_space',lambda _:None)
    def model(prompt,schema,**kwargs):
        if 'cues' in schema['properties']:return {'cues':cues}
        if 'pages' in schema['properties']:
            assert schema['properties']['pages']['items']['properties']['start_cue']['enum']==[0]
            value=plan();value['pages'][0]['end_cue']=0;value['pages'][0]['elements'][1]['cue_index']=0;return value
        return {'supported':True,'reason':'synthetic'}
    def process(args,env,timeout,cancelled):
        request=json.loads(Path(args[3]).read_text());assert len(request['cues'])==1 and len(request['cues'][0]['parts'])==3
        result=Path(args[5]);result.write_text('{}')
        if args[-1]!='--validate':result.with_suffix('.mp4').write_bytes(b'synthetic')
        return 0
    monkeypatch.setattr(service,'_process',process)
    result=service._produce({'episode':episode,'audio':base64.b64encode(raw).decode(),'source_context':{},'podcast_id':'p','episode_index':0,'source_resolver':'/source'},luna=model,model='synthetic',align=lambda *a,**k:alignment,text_lock=Lock(),asr_lock=Lock(),cancelled=Event())
    assert len(result['cues'])==1 and 'groups' not in result['alignment']

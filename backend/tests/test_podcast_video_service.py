"""取消須終止渲染並阻止後續模型請求；不使用真實教材或模型。"""
import importlib.util
from pathlib import Path
import sys
from threading import Event, Timer
from uuid import uuid4

import pytest
from test_podcast_video_layout import design

spec=importlib.util.spec_from_file_location('podcast_video_service',Path(__file__).resolve().parents[2]/'ops/podcast/video_service.py')
service=importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def model_design(schema, kind='comparison'):
    page = design(kind, 2)['pages'][0]
    return {'pages': [{'layout': kind, 'title': page['title'],
                       'nodes': [{'label': n['label'], 'text': n['text'], 'anchor': '這是第一個', 'key_terms': ['必要條件']}
                                 for n in page['nodes']],
                       'relations': []} for _ in range(schema['properties']['pages']['minItems'])]}


def test_cancel_before_start_never_calls_model():
    body={'job_id':str(uuid4()),'job_token':str(uuid4())}
    service.podcast_process.cancel(body)
    with pytest.raises(ValueError,match='PODCAST_CANCELLED'):
        with service.podcast_process.job(body) as cancelled:
            service.produce(body,luna=lambda *_:pytest.fail('cancelled job called model'),model='synthetic',align=None,text_lock=None,asr_lock=None,cancelled=cancelled)


def test_cancel_running_renderer_stops_child():
    cancelled=Event()
    timer=Timer(.3,cancelled.set);timer.start()
    try:
        with pytest.raises(ValueError,match='VIDEO_CANCELLED'):
            service._process([sys.executable,'-c','import time; time.sleep(30)'],None,10,cancelled)
    finally:timer.cancel()



@pytest.mark.parametrize('kind',['comparison','flow'])
@pytest.mark.parametrize('failure',[None,'layout','render'])
def test_one_storyboard_call_without_quality_review_or_rewrite(tmp_path,monkeypatch,failure,kind):
    import base64,json
    from hashlib import sha256
    from threading import Lock
    from test_podcast_video_plan import sample,plan
    episode,cues,alignment=sample();audio=b'synthetic audio';episode['audio']['sha256']=sha256(audio).hexdigest()
    context = {}
    for i, claim in enumerate(episode['claims']):
        claim.update(label=f'觀念 {i}', text=f'來源公式 x={i+3}，只適用 N>0。')
        context[str(i)] = {'source_name': 'synthetic.pdf', 'normalized_page': i+1,
            'blocks': [{'evidence_id': claim['evidence'][0]['evidence_id'], 'text': claim['text']}]}
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON',sys.executable)
    monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR',str(tmp_path))
    monkeypatch.setattr(service,'ensure_space',lambda _:None)
    # 同名段落仍依原稿身分分頁，不以文字猜測邊界。
    for beat in episode['script']['segments']:beat['title']='同名標題'
    calls=[];rendered=[]
    def model(prompt,schema,**kwargs):
        assert kwargs['timeout'] is None
        assert set(schema['properties'])=={'pages'}
        assert schema['properties']['pages']['minItems']==2
        source = json.loads(prompt[prompt.index('{"source":'):])['source']
        assert set(source) == {'pages', 'sources', 'page_context'}
        assert [p['blocks'][0]['text'] for p in source['page_context']] == [c['text'] for c in episode['claims']]
        assert [s['block_indices'] for s in source['sources']] == [[0], [1]]
        assert '"evidence_id"' not in prompt and all('claim' not in s for s in source['sources'])
        assert 'anchor' in prompt and 'key_terms' in prompt
        calls.append(prompt);return model_design(schema,kind)
    def process(args,env,timeout,cancelled):
        assert timeout is None
        result=Path(args[5])
        if args[-1]=='--validate':
            if failure=='layout':result.write_text('{"error":"VIDEO_LAYOUT_INVALID:bad bounds"}');return 1
            from runtime.podcast_video_layout import compile_layout
            request=json.loads(Path(args[3]).read_text())
            assert all('cue_index' not in n for p in request['semantic_plan']['pages'] for n in p['nodes'])
            # 合成對齊只核對分頁與 anchor 換算；實際 timeline 驗證由 renderer 測試涵蓋。
            measured={'segments':[{'start':2*i,'end':2*i+2,'text':'\n'.join(p['text'] for p in c['parts'])}
                                  for i,c in enumerate(request['cues'])]}
            value=compile_layout(request['semantic_plan'],request['cues'],measured)
            assert [(p['start_cue'],p['end_cue']) for p in value['pages']]==[(0,0),(1,1)]
            assert all(('spotlight' in p)==(kind=='flow') and not any(e['kind']=='arrow' for e in p['elements']) for p in value['pages'])
            # 只有逐字出自節點文字的重點詞會留下；第一頁 anchor 對得到，第二頁對不到則退回頁首。
            assert {m['quote'] for p in value['pages'] for m in p['emphasis']}=={'必要條件'}
            result.write_text(json.dumps({'valid':True,'plan':value}))
        else:
            if failure=='render':return 1
            rendered.append(True);result.write_text('{}');result.with_suffix('.mp4').write_bytes(b'synthetic video')
        return 0
    monkeypatch.setattr(service,'_process',process)
    body={'episode':episode,'audio':base64.b64encode(audio).decode(),'source_context':context,'podcast_id':'p','episode_index':0,'source_resolver':'/source'}
    kwargs=dict(luna=model,model='synthetic',align=lambda *a,**k:alignment,text_lock=Lock(),asr_lock=Lock(),cancelled=Event())
    if failure:
        with pytest.raises(ValueError,match='VIDEO_LAYOUT_INVALID' if failure=='layout' else 'VIDEO_RENDER_FAILED'):
            service.produce(body,**kwargs)
        assert not rendered
    else:
        result=service.produce(body,**kwargs)
        assert 'review' not in result and rendered==[True]
    assert len(calls)==1 and not list(tmp_path.iterdir())


def test_measured_grouping_reaches_storyboard_and_render_without_dropping_turns(tmp_path,monkeypatch):
    import base64,json
    from hashlib import sha256
    from threading import Lock
    from test_podcast_video_plan import sample,plan
    episode,cues,alignment=sample();raw=b'synthetic';episode['audio']['sha256']=sha256(raw).hexdigest()
    for i, claim in enumerate(episode['claims']):
        claim.update(label=f'觀念 {i}', text=f'有來源的觀念 {i}')
    alignment.update(groups=[[0,1]],starts=[0],anchors=[alignment['anchors'][0]])
    monkeypatch.setenv('STUDYDY_VIDEO_PYTHON',sys.executable);monkeypatch.setenv('STUDYDY_VIDEO_WORK_DIR',str(tmp_path));monkeypatch.setattr(service,'ensure_space',lambda _:None)
    def model(prompt,schema,**kwargs):
        if 'pages' in schema['properties']:
            assert schema['properties']['pages']['minItems']==schema['properties']['pages']['maxItems']==1
            return model_design(schema)
        return {k:{'passed':True,'reason':'synthetic'} for k in ('correctness','teaching_quality')}
    def process(args,env,timeout,cancelled):
        request=json.loads(Path(args[3]).read_text());assert len(request['cues'])==1 and len(request['cues'][0]['parts'])==3
        result=Path(args[5])
        value=plan();value['schema']='podcast-storyboard/v2';value['pages'][0]['end_cue']=0;value['pages'][0]['elements'][1]['cue_index']=0
        result.write_text(json.dumps({'valid':True,'plan':value}) if args[-1]=='--validate' else '{}')
        if args[-1]!='--validate':result.with_suffix('.mp4').write_bytes(b'synthetic')
        return 0
    monkeypatch.setattr(service,'_process',process)
    result=service.produce({'episode':episode,'audio':base64.b64encode(raw).decode(),'source_context':{},'podcast_id':'p','episode_index':0,'source_resolver':'/source'},luna=model,model='synthetic',align=lambda *a,**k:alignment,text_lock=Lock(),asr_lock=Lock(),cancelled=Event())
    assert len(result['cues'])==1 and 'groups' not in result['alignment']

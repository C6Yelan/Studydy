"""動態狀態與真實像素回歸；合成內容不作為教學品質驗收。"""
from copy import deepcopy
from hashlib import sha256
import pytest
from runtime.podcast_video_layout import compile_layout, semantic_schema
from runtime.podcast_video_motion import scene_state, transition
from runtime.podcast_video_plan import validate_plan, validate_visual_timing
from runtime import podcast_video_render as render
from test_podcast_video_layout import design, caption_exchange

pytest.importorskip('PIL')


def scene(kind='exchange', count=2):
    value, timeline = caption_exchange()
    if kind != 'exchange':
        value = design(kind, count, end=0)
        for i, node in enumerate(value['pages'][0]['nodes']):
            node.update(cue_index=0, caption_index=i, reveal=True, focus=True)
        for i, edge in enumerate(value['pages'][0]['relations']):
            edge.update(cue_index=0, caption_index=i+1)
    plan = compile_layout(value, [{}], timeline, motion=True)
    return plan, timeline


def board(plan, timeline, t):
    return render.draw_frame(t, timeline, plan, render.layouts(plan)).crop((0,0,1920,852)).tobytes()


@pytest.mark.parametrize('kind', ['concept', 'comparison', 'flow', 'exchange'])
def test_entrance_is_continuous_and_preserves_existing_geometry(kind):
    plan, timeline = scene(kind); page = plan['pages'][0]
    initial = scene_state(0, timeline, page); partial = scene_state(.1, timeline, page)
    assert initial[0]['entrance'] == 0 < partial[0]['entrance'] < 1
    assert scene_state(.5, timeline, page)[0]['entrance'] == 1
    assert len({board(plan,timeline,t) for t in (0,.1,.5)}) == 3
    before = deepcopy(page)
    for t in (7,2.1,.1,4.3): board(plan,timeline,t)
    assert page == before


def test_caption_boundary_reveal_focus_and_emphasis_never_leak_into_the_past():
    plan, timeline = scene('comparison'); page = plan['pages'][0]
    assert scene_state(1.999,timeline,page)[1]['entrance'] == 0
    assert scene_state(2,timeline,page)[1]['entrance'] == 0
    partial = scene_state(2.1,timeline,page)
    assert 0 < partial[1]['entrance'] < 1
    assert 0 < partial[0]['focus'] < 1 and 0 < partial[1]['focus'] < 1
    assert scene_state(2.5,timeline,page)[0]['focus'] == 0
    assert scene_state(2.5,timeline,page)[1]['focus'] == 1
    assert scene_state(7.9,timeline,page)[0]['opacity'] == .65  # 持續可讀，不移除舊概念。
    without = deepcopy(plan); without['pages'][0]['emphasis'] = []
    assert board(plan,timeline,1.999) == board(without,timeline,1.999)
    assert board(plan,timeline,2.5) != board(without,timeline,2.5)
    assert board(plan,timeline,4.1) == board(without,timeline,4.1)


@pytest.mark.parametrize('kind,count', [('flow',6), ('exchange',2)])
def test_relation_draw_on_and_directed_progression_use_only_existing_arrow(kind,count):
    plan,timeline = scene(kind,count) if count==2 else (None,None)
    if count==6:
        value=design(kind,count); timeline={'duration':8,'segments':[{'start':2*i,'end':2*i+2,'title':str(i)} for i in range(4)]}
        plan=compile_layout(value,[{}]*4,timeline,motion=True)
    page=plan['pages'][0]; group_index=count+1 if kind=='exchange' else count
    group=page['motion']['groups'][group_index]
    start=2
    before=scene_state(start-.001,timeline,page)[group_index]
    first=scene_state(start+.15,timeline,page)[group_index]
    last=scene_state(start+.5,timeline,page)[group_index]
    done=scene_state(start+.8,timeline,page)[group_index]
    assert before['draw']==0 and before['token'] is None
    assert 0<first['draw']<last['draw']==1
    assert 0<first['token']<last['token']<1 and done['token'] is None
    arrow=next(page['elements'][j] for j in group['elements'] if page['elements'][j]['kind']=='arrow')
    if kind=='exchange': assert arrow['w']<0  # 回覆沿原本的反向箭頭移動。
    settled=scene_state(start+.8,timeline,page)
    assert settled[group['target']]['focus']==1
    # v3 trace 不另外覆蓋第二顆指示；文字 outline 仍保留。
    without=deepcopy(plan)
    without['pages'][0]['emphasis']=[m for m in page['emphasis'] if m['kind']!='trace']
    assert board(plan,timeline,start+.2)==board(without,timeline,start+.2)


def two_pages():
    value=design('concept',1,end=0); other=deepcopy(value['pages'][0])
    other.update(title='下一個模型',start_cue=1,end_cue=1)
    other['nodes'][0].update(label='另一個概念',cue_index=1)
    value['pages'].append(other)
    timeline={'duration':4,'segments':[{'start':0,'end':2,'title':'一'},{'start':2,'end':4,'title':'二'}]}
    return compile_layout(value,[{},{}],timeline,motion=True),timeline


def test_crossfade_is_short_has_no_future_page_and_uses_current_footer():
    import math
    plan,timeline=two_pages(); page=plan['pages'][1]
    assert transition(2,timeline,page)==0
    assert 0<transition(2.1,timeline,page)<1
    assert transition(2.2,timeline,page)==1
    assert board(plan,timeline,2)==board(plan,timeline,math.nextafter(2,-math.inf))
    assert len({board(plan,timeline,t) for t in (1.99,2.1,2.3)})==3
    frame=render.draw_frame(2,timeline,plan,render.layouts(plan))
    current=deepcopy(plan); current['schema']='podcast-storyboard/v2'
    assert frame.crop((0,852,1920,1080)).tobytes()==render.draw_frame(2,timeline,current,render.layouts(current)).crop((0,852,1920,1080)).tobytes()


@pytest.mark.parametrize('rate',[.5,1,2])
def test_repeat_render_backward_forward_seek_and_playback_rate_share_media_clock(rate):
    plan,timeline=two_pages()
    times=[0,.1,1.99,2,2.05,2.3,3.9]
    expected={t:sha256(board(plan,timeline,t)).hexdigest() for t in times}
    for media_time in reversed(times):
        elapsed=media_time/rate
        assert sha256(board(plan,timeline,elapsed*rate)).hexdigest()==expected[media_time]
    for t in times: assert sha256(board(plan,timeline,t)).hexdigest()==expected[t]


@pytest.mark.parametrize('change',['duplicate','direction','raw_time','raw_path','missing','unknown_version','hidden_node','split_group','early_focus','foreign_caption','wrong_cue','offscreen','overflow'])
def test_motion_cannot_bypass_trusted_layout_or_measured_timing(change):
    plan,timeline=scene(); page=plan['pages'][0]; groups=page['motion']['groups']
    if change=='duplicate':groups[1]['elements'].append(0)
    elif change=='direction':groups[2].update(source=1,target=0)
    elif change=='raw_time':groups[2]['duration']=.5
    elif change=='raw_path':groups[2]['path']=[[0,0],[1,1]]
    elif change=='missing':del page['motion']
    elif change=='unknown_version':plan['schema']='podcast-storyboard/v4'
    elif change=='hidden_node':page['reveal'].append({'start_cue':0,'caption_index':3,'elements':groups[0]['elements']})
    elif change=='split_group':page['reveal'].append({'start_cue':0,'caption_index':3,'elements':[groups[0]['elements'][1]]})
    elif change=='early_focus':groups[3]['caption_index']=0
    elif change=='foreign_caption':groups[3]['caption_index']=99
    elif change=='wrong_cue':groups[3]['start_cue']=1
    elif change=='offscreen':page['elements'][0]['x']=1900
    elif change=='overflow':page['elements'][0]['text']='測'*160
    with pytest.raises(ValueError,match='VIDEO_(STORYBOARD|LAYOUT)_INVALID'):
        validate_plan(plan,[{}]);validate_visual_timing(plan,timeline);render.layouts(plan)


def test_legacy_schema_and_geometry_are_preserved_when_motion_is_not_requested():
    value,timeline=caption_exchange()
    legacy=compile_layout(value,[{}],timeline)
    modern=compile_layout(value,[{}],timeline,motion=True)
    modern['schema']='podcast-storyboard/v2';del modern['pages'][0]['motion']
    assert legacy==modern
    import json
    schema=json.dumps(semantic_schema([{}],4))
    assert all(field not in schema for field in ('"duration"','"path"','"x"','"y"'))
    assert board(legacy,timeline,.1)==board(modern,timeline,.1)


def test_motion_render_still_checks_audio_hash_and_source_script(tmp_path):
    from test_podcast_video_plan import sample
    episode,cues,alignment=sample();audio=tmp_path/'audio.wav';audio.write_bytes(b'synthetic')
    value=design('comparison',2,end=1)
    plan=compile_layout(value,cues,motion=True)
    request={'podcast_id':'p','episode_index':0,'episode':episode,'cues':cues,'alignment':alignment,'source_resolver':'/source','plan':plan}
    with pytest.raises(ValueError,match='VIDEO_SOURCE_CHANGED'):render.render(request,audio,tmp_path/'no.mp4')
    episode['audio']['sha256']=sha256(audio.read_bytes()).hexdigest()
    cues[0]['parts'][0]['text']='未授權替換講稿'
    with pytest.raises(ValueError,match='VIDEO_TRANSCRIPT_INVALID'):render.render(request,audio,tmp_path/'no.mp4')
    assert not (tmp_path/'no.mp4').exists()


def test_tied_anchors_do_not_invent_order_and_short_windows_finish_in_time():
    value,timeline=caption_exchange()
    value['pages'][0]['relations'][1]['caption_index']=0
    plan=compile_layout(value,[{}],timeline,motion=True)
    state=scene_state(.1,timeline,plan['pages'][0])
    assert state[2]['focus']==state[3]['focus']==1
    assert state[2]['token']==state[3]['token']
    for segment in timeline['segments']:segment['end']/=100
    for caption in timeline['captions']:
        caption['start']/=100;caption['end']/=100
    timeline['duration']/=100
    plan=compile_layout(value,[{}],timeline,motion=True)
    states=scene_state(.019,timeline,plan['pages'][0])
    assert states[2]['entrance']==states[2]['draw']==1 and states[2]['token'] is None


def test_circle_enters_as_a_single_labelled_shape():
    plan,timeline=scene('concept');page=plan['pages'][0]
    page['elements'][0]['kind']='circle'
    validate_plan(plan,[{}]);validate_visual_timing(plan,timeline)
    assert len({board(plan,timeline,t) for t in (0,.1,.5)})==3


def test_text_pixels_really_fade_and_dim_on_rgb_canvas():
    from PIL import ImageColor
    plan,timeline=scene('comparison');e=plan['pages'][0]['elements'][0]
    positions=render.layouts(plan)
    frames=[render.draw_frame(t,timeline,plan,positions) for t in (.05,.2,.5,2.5)]
    area=(e['x'],e['y'],e['x']+e['w'],e['y']+e['h'])
    rgb=ImageColor.getrgb(render.PALETTE[e['color']])
    full=frames[2].crop(area)
    # 找完全覆蓋的字形內部，避免僅驗證背景／框線的透明度。
    point=next((x,y) for y in range(20,80) for x in range(20,100) if full.getpixel((x,y))==rgb)
    colors=[frame.getpixel((e['x']+point[0],e['y']+point[1])) for frame in frames]
    assert sum(colors[0])>sum(colors[1])>sum(colors[2])
    assert sum(colors[3])>sum(colors[2]) and sum(colors[3])<sum(colors[0])


@pytest.mark.parametrize('motion',[False,True])
def test_renderer_cli_explicit_opt_in_preserves_rolling_service_compatibility(tmp_path,motion):
    import json,subprocess,sys
    from test_podcast_video_plan import sample
    episode,cues,alignment=sample()
    request={'podcast_id':'p','episode_index':0,'episode':episode,'cues':cues,'alignment':alignment,
             'source_resolver':'/source','semantic_plan':design('comparison',2,end=1)}
    source=tmp_path/'request.json';result=tmp_path/'result.json';source.write_text(json.dumps(request))
    flags=['--motion','--validate'] if motion else ['--validate']
    completed=subprocess.run([sys.executable,'-m','runtime.podcast_video_render',str(source),str(tmp_path/'unused.wav'),str(result),*flags],capture_output=True,timeout=10)
    assert completed.returncode==0
    compiled=json.loads(result.read_text())
    assert compiled['valid'] and compiled['plan']['schema']==('podcast-storyboard/v3' if motion else 'podcast-storyboard/v2')
    assert ('motion' in compiled['plan']['pages'][0])==motion


def test_first_delayed_focus_smoothly_leaves_the_page_preview():
    value=design('comparison',2,end=1)
    value['pages'][0]['nodes'][0]['cue_index']=1
    value['pages'][0]['nodes'][1].update(cue_index=1,caption_index=1)
    timeline={'duration':4,'segments':[{'start':0,'end':2},{'start':2,'end':4}],
              'captions':[{'start':2,'end':3},{'start':3,'end':4}]}
    plan=compile_layout(value,[{},{}],timeline,motion=True);page=plan['pages'][0]
    assert scene_state(1.9,timeline,page)[1]['focus']==1
    assert scene_state(2,timeline,page)[1]['focus']==1
    assert 0<scene_state(2.1,timeline,page)[1]['focus']<1
    assert scene_state(2.4,timeline,page)[1]['focus']==0

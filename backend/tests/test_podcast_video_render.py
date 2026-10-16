"""版面回饋應一次涵蓋所有溢位，避免有限重做只修到第一個框。"""
import pytest
from runtime import podcast_video_render as render


def test_reports_all_overflowing_text_boxes_in_one_pass(monkeypatch):
    # 此測試只核對批次錯誤回饋；真實字型寬度另由 CPU 渲染驗證。
    monkeypatch.setattr(render,'lines',lambda text,size,width:text.split('\n'))
    plan={'pages':[{'elements':[
        {'kind':'box','text':'第一行\n第二行\n第三行','x':100,'y':250,'w':600,'h':180,'size':42},
        {'kind':'box','text':'第一行\n第二行\n第三行','x':1000,'y':250,'w':600,'h':180,'size':52},
    ]}]}
    with pytest.raises(ValueError) as failure:render.layouts(plan)
    message=str(failure.value)
    assert message.startswith('VIDEO_LAYOUT_INVALID:')
    assert 'page=0,element=0' in message and 'height>=188' in message
    assert 'page=0,element=1' in message and 'height>=218' in message


def test_temporary_emphasis_fades_out_and_seeking_never_leaves_a_mark():
    assert render.emphasis_opacity(-1,0,4)==0
    assert render.emphasis_opacity(0,0,4)==0
    assert render.emphasis_opacity(.5,0,4)==1
    assert 0<render.emphasis_opacity(3.9,0,4)<1
    assert render.emphasis_opacity(4,0,4)==0
    assert render.emphasis_opacity(20,0,4)==0
    assert render.emphasis_opacity(1,0,4)==1


def test_latin_protocol_and_unit_tokens_move_to_the_next_line_intact():
    pytest.importorskip('PIL')
    width = render.font(28).getlength('條件 TCP')
    values = render.lines('條件較長 TCP MB/s', 28, width)
    assert ''.join(values) == '條件較長 TCP MB/s'
    assert any('TCP' in line for line in values) and any('MB/s' in line for line in values)


def test_arrow_with_visual_gap_renders_without_a_connection_distance_gate():
    pytest.importorskip('PIL')
    from test_podcast_video_plan import plan, sample
    from runtime.podcast_video_plan import validate_plan, timeline_for_video
    from PIL import ImageColor
    episode,cues,alignment=sample();value=plan()
    # 箭頭與右框留 55px 空白，仍可正常繪製；關係含義由內容審查核對。
    value['pages'][0]['elements'].append({'kind':'arrow','cue_index':0,'text':'',
        'x':700,'y':350,'w':245,'h':0,'size':28,'color':'ink','filled':False})
    validate_plan(value,cues)
    timeline=timeline_for_video('p',0,episode,cues,alignment,'/source')
    frame=render.draw_frame(.1,timeline,value,render.layouts(value))
    assert frame.getpixel((940,350))==ImageColor.getrgb(render.PALETTE['ink'])


def test_full_page_is_visible_on_entry_and_only_selected_emphasis_changes():
    pytest.importorskip('PIL')  # 真實像素驗證使用獨立影片環境的 Pillow／字型。
    from test_podcast_video_plan import plan
    from copy import deepcopy
    from PIL import ImageColor
    value=plan();page=value['pages'][0];page['end_cue']=2
    page['elements'].append({'kind':'arrow','cue_index':1,'text':'','x':700,'y':350,
                             'w':300,'h':0,'size':28,'color':'ink','filled':False})
    page['emphasis']=[{'element_index':1,'start_cue':1,'end_cue':1,'kind':'outline','quote':''}]
    next_page=deepcopy(page);next_page.update(title='下一頁',start_cue=3,end_cue=3,emphasis=[])
    for e in next_page['elements']:e['cue_index']=3
    value['pages'].append(next_page)
    timeline={'duration':8,'segments':[{'start':i*2,'end':i*2+2,'text':f'第{i}段講解'} for i in range(4)]}
    text_layouts=render.layouts(value)
    def frame(t):return render.draw_frame(t,timeline,value,text_layouts)
    def board(t):return frame(t).crop((0,0,1920,852)).tobytes()
    initial=frame(0)
    # 後段才說的右框與完整箭頭在第一影格已存在，且未靠粗框自動強調。
    assert initial.getpixel((1000,250))==ImageColor.getrgb(render.PALETTE['blue'])
    assert initial.getpixel((950,350))==ImageColor.getrgb(render.PALETTE['ink'])
    assert board(0)==board(1)==board(4)==board(5.9)
    assert board(2.5)!=board(0)
    assert board(6)!=board(5.9)  # 到主題邊界才換整頁。
    assert board(1)==board(0)  # 往回跳不留下標記。


def test_progressive_reveal_and_reverse_seek_are_deterministic():
    pytest.importorskip('PIL')
    from runtime.podcast_video_render import layouts, draw_frame
    from runtime.podcast_video_plan import validate_plan, timeline_for_video
    from test_podcast_video_plan import sample,plan
    episode,cues,alignment=sample();value=plan();value['schema']='podcast-storyboard/v2'
    page=value['pages'][0];page['emphasis']=[];page['reveal']=[{'start_cue':1,'elements':[1]}]
    page['elements'][1]['cue_index']=1
    validate_plan(value,cues)
    timeline=timeline_for_video('p',0,episode,cues,alignment,'/source')
    layout=layouts(value);cache={};before=draw_frame(.1,timeline,value,layout,scene_cache=cache)
    after=draw_frame(timeline['segments'][1]['start']+.1,timeline,value,layout,scene_cache=cache)
    again=draw_frame(.1,timeline,value,layout,scene_cache=cache)
    assert before.tobytes()==again.tobytes()
    e=page['elements'][1];box=(e['x'],e['y'],e['x']+e['w'],e['y']+e['h'])
    assert before.crop(box).tobytes()!=after.crop(box).tobytes()


@pytest.mark.parametrize('starts',[(0,0),(1,0)])
def test_page_entry_and_unordered_reveal_groups_render_like_implicit_visibility(starts):
    pytest.importorskip('PIL')
    from copy import deepcopy
    from test_podcast_video_plan import plan, sample
    from runtime.podcast_video_plan import validate_plan, timeline_for_video
    episode,cues,alignment=sample();value=plan();page=value['pages'][0]
    page['emphasis']=[]
    page['reveal']=[{'start_cue':starts[0],'elements':[1]}, {'start_cue':starts[1],'elements':[0]}]
    validate_plan(value,cues)
    implicit=deepcopy(value)
    implicit['pages'][0]['reveal']=[g for g in page['reveal'] if g['start_cue']>0]
    timeline=timeline_for_video('p',0,episode,cues,alignment,'/source')
    layout=render.layouts(value)
    for t in (.1,2.1,.1):
        assert render.draw_frame(t,timeline,value,layout).tobytes()==render.draw_frame(t,timeline,implicit,layout).tobytes()


def test_expanded_board_is_not_covered_by_a_baked_title_footer():
    pytest.importorskip('PIL')
    from PIL import ImageColor
    from test_podcast_video_plan import sample, plan
    from runtime.podcast_video_plan import validate_plan, timeline_for_video
    episode, cues, alignment = sample(); value = plan()
    value['pages'][0]['elements'][0].update(y=820, h=200)
    validate_plan(value, cues)
    timeline = timeline_for_video('p', 0, episode, cues, alignment, '/source')
    frame = render.draw_frame(.1, timeline, value, render.layouts(value))
    assert frame.getpixel((100, 1000)) == ImageColor.getrgb(render.PALETTE['teal'])
    assert frame.getpixel((1800, 1000)) == ImageColor.getrgb(render.BACKGROUND)


@pytest.mark.parametrize('text', ['說明文字。下一句，仍保留！', '文字「完整說明」結束。', '資料（含表頭）繼續。', 'TCP/IP，MB/s。'])
def test_wrapping_keeps_punctuation_with_its_text_without_changing_content(text):
    pytest.importorskip('PIL')
    width = render.font(28).getlength('說明文字')
    rows = render.lines(text, 28, width)
    assert ''.join(rows) == text
    assert all(render.font(28).getlength(row) <= width for row in rows)
    assert all(not row.startswith(tuple('，。！？；：、）」』】')) for row in rows[1:])
    assert all(not row.endswith(tuple('（「『【')) for row in rows[:-1])


def test_wrapping_preserves_explicit_paragraphs_and_splits_long_identifiers():
    pytest.importorskip('PIL')
    text = 'Content-Transfer-Encoding。\n第二段。'
    width = render.font(28).getlength('第二段。')
    rows = render.lines(text,28,width)
    assert ''.join(rows) == text.replace('\n','')
    assert rows[-1] == '第二段。'
    assert all(render.font(28).getlength(row) <= width for row in rows)


def test_failed_renderer_keeps_private_reproduction_inputs(tmp_path):
    import json, subprocess, sys
    job = tmp_path / 'episode'
    job.mkdir()
    source, audio, result = job/'request.json', job/'audio.wav', job/'result.json'
    source.write_text(json.dumps({'episode': {'audio': {'sha256': '0'*64}}}))
    audio.write_bytes(b'local-test-audio')
    completed = subprocess.run([sys.executable, '-m', 'runtime.podcast_video_render',
        str(source), str(audio), str(result)], capture_output=True, timeout=10)
    assert completed.returncode == 1
    assert json.loads(result.read_text()) == {'error': 'VIDEO_SOURCE_CHANGED'}
    saved, = (tmp_path/'failures').iterdir()
    assert (saved/'request.json').read_bytes() == source.read_bytes()
    assert (saved/'audio.wav').read_bytes() == audio.read_bytes()
    assert 'VIDEO_SOURCE_CHANGED' in (saved/'traceback.txt').read_text()
    assert saved.stat().st_mode & 0o777 == 0o700
    assert (saved/'request.json').stat().st_mode & 0o777 == 0o600
    assert not completed.stdout and not completed.stderr

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


def test_connection_arrow_must_reach_a_node_but_direction_hint_is_allowed():
    a={'kind':'box','x':100,'y':300,'w':400,'h':200}
    b={'kind':'box','x':900,'y':300,'w':400,'h':200}
    arrow={'kind':'arrow','x':500,'y':400,'w':400,'h':0}
    page={'elements':[a,b,arrow]}
    assert render.connection_errors(page,[])==[]
    arrow['w']=200
    assert 'element=2' in render.connection_errors(page,[])[0]
    page['elements'].append({'kind':'text'})
    assert render.connection_errors(page,[(3,530,340,760,380)])==[]


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
    layout=layouts(value);before=draw_frame(.1,timeline,value,layout)
    after=draw_frame(timeline['segments'][1]['start']+.1,timeline,value,layout)
    again=draw_frame(.1,timeline,value,layout)
    assert before.tobytes()==again.tobytes()
    e=page['elements'][1];box=(e['x'],e['y'],e['x']+e['w'],e['y']+e['h'])
    assert before.crop(box).tobytes()!=after.crop(box).tobytes()

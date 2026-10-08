"""實際像素與訊息狀態回歸；不以合成例子宣稱真實教材品質。"""
from copy import deepcopy
import pytest
from runtime.podcast_video_layout import compile_layout
from runtime.podcast_video_interaction import interaction_state
from runtime.podcast_video_plan import validate_plan, validate_visual_timing
from runtime.podcast_video_render import layouts, draw_frame
from test_podcast_video_layout import caption_exchange


def scene():
    design, timeline = caption_exchange()
    page=design['pages'][0];page['layout']='interaction'
    for i,node in enumerate(page['nodes']):
        node.update(icon='computer' if i==0 else 'server',reveal=False,caption_index=None,cue_index=0)
    for i,edge in enumerate(page['relations']):
        edge['outcome']='lost' if i==0 else 'delivered'
    return design,timeline


def test_longer_motion_delivery_loss_and_reverse_messages_have_distinct_pixels():
    design,timeline=scene();plan=compile_layout(design,[{}],timeline,motion=True)
    page=plan['pages'][0];positions=layouts(plan)
    # 第一訊息遺失，接收端不會變成已接收；下一訊息依其反向 source/target 演示。
    early=interaction_state(.1,timeline,page)
    moving=interaction_state(1,timeline,page)
    lost=interaction_state(1.8,timeline,page)
    received=interaction_state(3.8,timeline,page)
    assert early['current']['stage']=='sending'
    assert moving['current']['stage']=='transit' and 0<moving['current']['position']<1
    assert lost['current']['stage']=='lost' and lost['actors'][1]=='未收到此訊息'
    assert received['current']['stage']=='delivered' and received['actors'][0]=='已接收'
    hashes={t:draw_frame(t,timeline,plan,positions).crop((100,220,1820,835)).tobytes() for t in (.1,.8,1.2,1.8,2.8,3.8)}
    assert len(set(hashes.values()))==6
    for t in reversed(list(hashes)):
        assert draw_frame(t,timeline,plan,positions).crop((100,220,1820,835)).tobytes()==hashes[t]
    assert interaction_state(.1,timeline,page)==early


@pytest.mark.parametrize('change',['icon','outcome','tie','hidden','direction','raw_time'])
def test_interaction_rejects_unsupported_objects_ambiguous_order_and_future_actors(change):
    design,timeline=scene();page=design['pages'][0]
    if change=='icon':page['nodes'][0]['icon']='custom-code'
    elif change=='outcome':page['relations'][0]['outcome']='automatically-retry'
    elif change=='tie':page['relations'][1]['caption_index']=page['relations'][0]['caption_index']
    elif change=='hidden':page['nodes'][0].update(cue_index=0,reveal=True,caption_index=1)
    elif change=='direction':page['relations'][0]['target']=9
    elif change=='raw_time':page['relations'][0]['duration']=20
    with pytest.raises(ValueError,match='VIDEO_STORYBOARD_INVALID'):
        compile_layout(design,[{}],timeline,motion=True)


def test_compiled_interaction_keeps_source_labels_and_validates_on_read():
    design,timeline=scene();before=deepcopy(design)
    plan=compile_layout(design,[{}],timeline,motion=True)
    assert design==before
    assert validate_plan(plan,[{}])==plan
    validate_visual_timing(plan,timeline)
    for label in [r['label'] for r in design['pages'][0]['relations']]:
        assert any(e['text']==label for e in plan['pages'][0]['elements'])
    with pytest.raises(ValueError,match='motion renderer'):
        compile_layout(design,[{}],timeline)

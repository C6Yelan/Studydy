"""語意分鏡的真實字型／像素驗證；不呼叫模型。"""
from copy import deepcopy
import json
import pytest
from runtime.podcast_video_layout import compile_layout, semantic_schema
from runtime.podcast_video_render import layouts, emphasis_layouts, draw_frame
from runtime.podcast_video_plan import validate_plan, validate_visual_timing


def design(kind='exchange', count=2, end=3):
    nodes = [{'label': f'參與者{i+1}', 'text': '' if kind == 'exchange' else '來源支持的必要條件與說明。',
              'cue_index': 0, 'reveal': False, 'focus': False} for i in range(count)]
    relations = ([{'source': i % 2, 'target': 1-i % 2, 'label': label, 'cue_index': i,
                  'reveal': True, 'focus': True} for i, label in enumerate(('FIN', 'ACK', 'FIN', 'ACK'))]
                 if kind == 'exchange' else
                 [{'source': i, 'target': i+1, 'label': '', 'cue_index': min(i+1, end),
                   'reveal': True, 'focus': True} for i in range(count-1)] if kind == 'flow' else [])
    return {'pages': [{'layout': kind, 'title': '雙方獨立完成傳送', 'start_cue': 0, 'end_cue': end,
                       'nodes': nodes, 'relations': relations}]}


def test_model_schema_has_semantic_relations_but_no_geometry_or_compiled_element_indices():
    raw = json.dumps(semantic_schema(2))
    for field in ('"x"', '"y"', '"w"', '"h"', '"element_index"', '"size"', '"cue_index"', '"caption_index"', '"start_cue"', '"end_cue"', '"reveal"', '"focus"'):
        assert field not in raw
    assert all(kind in raw for kind in ('concept', 'comparison', 'flow', 'exchange'))


@pytest.mark.parametrize('kind,count', [('concept', n) for n in range(1, 7)] +
                         [('comparison', 2), ('comparison', 3)] + [('flow', n) for n in range(1, 7)] + [('exchange', 2)])
def test_supported_layouts_fit_measured_text_without_model_coordinates(kind, count):
    pytest.importorskip('PIL')
    value = design(kind, count); before = deepcopy(value)
    if kind != 'exchange':
        for node in value['pages'][0]['nodes']:
            node.update(label='標' * 16, text='字' * 64)
        before = deepcopy(value)
    result = compile_layout(value, [{}]*4)
    positions = layouts(result)
    emphasis_layouts(result, positions)
    assert result['schema'] == 'podcast-storyboard/v2' and value == before
    assert result == compile_layout(value, [{}]*4)


def test_exchange_preserves_packet_direction_and_deterministic_reveal_on_reverse_seek():
    pytest.importorskip('PIL')
    value = design(); result = compile_layout(value, [{}]*4)
    arrows = [e for e in result['pages'][0]['elements'] if e['kind'] == 'arrow']
    assert [e['w'] > 0 for e in arrows] == [True, False, True, False]
    timeline = {'duration': 8, 'segments': [{'start': i*2, 'end': i*2+2, 'title': f'第{i+1}步'} for i in range(4)]}
    text = layouts(result)
    def board(t): return draw_frame(t, timeline, result, text).crop((0, 220, 1920, 810)).tobytes()
    first = board(.1); assert first != board(6.5) and first == board(.1)
    assert board(2.5) != board(3.99)  # 暫時 trace 會消退，節點及已揭示的箭頭仍存在。


def caption_exchange():
    value = design(end=0)
    for i, edge in enumerate(value['pages'][0]['relations']):
        edge.update(cue_index=0, caption_index=i)
    timeline = {'duration': 8, 'segments': [{'start': 0, 'end': 8, 'title': '完整四步驟'}],
                'captions': [{'start': i*2, 'end': i*2+2, 'text': f'第{i+1}個合成讀句'} for i in range(4)]}
    return value, timeline


def test_measured_caption_anchors_drive_visual_steps_without_splitting_teaching_scope():
    pytest.importorskip('PIL')
    value, timeline = caption_exchange(); before = deepcopy(timeline)
    result = compile_layout(value, [{}], timeline)
    text = layouts(result)
    def board(t): return draw_frame(t, timeline, result, text).crop((0, 220, 1920, 810)).tobytes()
    frames = [board(t) for t in (.5, 2.5, 4.5, 6.5)]
    assert len(set(frames)) == 4 and board(.5) == frames[0]
    assert len(timeline['segments']) == 1 and timeline == before
    assert [g['caption_index'] for g in result['pages'][0]['reveal']] == [1, 2, 3]


def test_unmeasured_caption_and_caption_outside_its_teaching_scope_are_rejected():
    pytest.importorskip('PIL')
    value, timeline = caption_exchange(); value['pages'][0]['relations'][0]['caption_index'] = 99
    with pytest.raises(ValueError, match='no measured anchor'): compile_layout(value, [{}], timeline)
    value, timeline = caption_exchange(); value['pages'][0]['end_cue'] = 1
    timeline['segments'] = [{'start': 0, 'end': 4}, {'start': 4, 'end': 8}]
    with pytest.raises(ValueError, match='must belong'): compile_layout(value, [{}, {}], timeline)


def test_same_teaching_cue_does_not_hide_a_connection_or_mark_that_appears_too_early():
    pytest.importorskip('PIL')
    value, timeline = caption_exchange(); result = compile_layout(value, [{}], timeline)
    bad = deepcopy(result)
    bad['pages'][0]['reveal'].append({'start_cue': 0, 'caption_index': 3, 'elements': [0]})
    validate_plan(bad, [{}])
    with pytest.raises(ValueError, match='connection must not precede'): validate_visual_timing(bad, timeline)
    bad = deepcopy(result)
    bad['pages'][0]['reveal'].append({'start_cue': 0, 'caption_index': 3, 'elements': [5]})
    with pytest.raises(ValueError, match='emphasis must not precede'): validate_visual_timing(bad, timeline)


@pytest.mark.parametrize('change', ['foreign', 'self', 'geometry', 'page', 'cue', 'label', 'hidden_node'])
def test_semantic_contract_keeps_invalid_references_out_of_renderer(change):
    pytest.importorskip('PIL')
    value = design(); page = value['pages'][0]
    if change == 'foreign': page['relations'][0]['target'] = 9
    elif change == 'self': page['relations'][0]['target'] = 0
    elif change == 'geometry': page['nodes'][0]['x'] = 100
    elif change == 'page': page['start_cue'] = 1
    elif change == 'cue': page['relations'][0]['cue_index'] = -1
    elif change == 'label': page['nodes'][0]['label'] = '標' * 17
    else:
        page['nodes'][1].update(cue_index=3, reveal=True)
        compiled = compile_layout(value, [{}]*4)
        # 第一個訊息需要的節點會在頁首出現；不讓箭頭先指向空白。
        assert all(2 not in g['elements'] for g in compiled['pages'][0]['reveal'])
        return
    with pytest.raises(ValueError, match='VIDEO_STORYBOARD_INVALID'):
        compile_layout(value, [{}]*4)

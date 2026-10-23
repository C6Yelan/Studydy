"""語意分鏡的真實字型／像素驗證；不呼叫模型。"""
from copy import deepcopy
import json
import pytest
from runtime.podcast_video_layout import compile_layout, semantic_schema
from runtime.podcast_video_render import layouts, emphasis_layouts, draw_frame
from runtime.podcast_video_plan import SAFE_BOTTOM, KEY_TERM_LIMIT

CAPTIONS = ['第一句先建立連線。', '第二句確認對方收到。', '第三句開始傳送資料。',
            '第四句結束並關閉。', '第五句保留紀錄。', '第六句完成檢查。']


def timeline(cues=1, captions=4):
    """每句字幕 2 秒，平均分給 teaching cue；cue 文字即其字幕逐字相接。"""
    per = captions // cues
    return {'duration': 2 * captions,
            'segments': [{'start': 2 * per * c, 'end': 2 * per * (c + 1), 'title': f'第{c+1}段',
                          'text': ''.join(CAPTIONS[c * per:(c + 1) * per])} for c in range(cues)],
            'captions': [{'start': 2 * i, 'end': 2 * i + 2, 'text': CAPTIONS[i]} for i in range(captions)]}


def design(kind='exchange', count=2, end=0):
    if kind == 'exchange':
        nodes = [{'label': f'參與者{i+1}', 'text': ''} for i in range(2)]
        relations = [{'source': i % 2, 'target': 1 - i % 2, 'label': label, 'anchor': CAPTIONS[i][:5], 'key_terms': []}
                     for i, label in enumerate(('FIN', 'ACK', 'FIN', 'ACK'))]
    else:
        nodes = [{'label': f'節點{i+1}', 'text': '來源支持的必要條件與說明。', 'anchor': CAPTIONS[i][:5], 'key_terms': []}
                 for i in range(count)]
        relations = []
    return {'sections': [{'start_cue': 0, 'end_cue': end, 'pages': [
        {'layout': kind, 'title': '雙方獨立完成傳送', 'anchor': '', 'nodes': nodes, 'relations': relations}]}]}


def steps(plan, page=0):
    return [(s['start_cue'], s.get('caption_index')) for s in plan['pages'][page]['spotlight']['steps']]


def test_model_schema_has_verbatim_anchor_and_terms_but_no_geometry_or_timing():
    raw = json.dumps(semantic_schema(2))
    for field in ('"x"', '"y"', '"w"', '"h"', '"element_index"', '"size"', '"cue_index"', '"caption_index"',
                  '"start_cue"', '"end_cue"', '"reveal"', '"focus"'):
        assert field not in raw
    assert all(kind in raw for kind in ('concept', 'comparison', 'flow', 'exchange'))
    variants = semantic_schema(1)['properties']['sections']['items']['properties']['pages']['items']['anyOf']
    for variant in variants:
        node = variant['properties']['nodes']['items']['properties']
        layout = variant['properties']['layout']['enum'][0]
        assert ('anchor' in node) == (layout != 'exchange')
        if layout != 'exchange':
            assert node['key_terms']['maxItems'] == 2
            assert node['key_terms']['items']['maxLength'] == 2 * KEY_TERM_LIMIT
    exchange = variants[3]['properties']['relations']['items']['properties']
    assert {'anchor', 'key_terms'} <= set(exchange)


@pytest.mark.parametrize('kind,count', [('concept', n) for n in range(1, 7)] +
                         [('comparison', 2), ('comparison', 3)] + [('flow', n) for n in range(1, 7)] + [('exchange', 2)])
def test_supported_layouts_fit_measured_text_above_the_subtitle_band(kind, count):
    pytest.importorskip('PIL')
    value = design(kind, count)
    if kind != 'exchange':
        for node in value['sections'][0]['pages'][0]['nodes']:
            node.update(label='標' * 16, text='字' * 64)
    before = deepcopy(value)
    measured = timeline(1, 6)
    result = compile_layout(value, [{}], measured)
    emphasis_layouts(result, layouts(result))
    assert result['schema'] == 'podcast-storyboard/v4' and value == before
    assert result == compile_layout(value, [{}], measured)
    page = result['pages'][0]
    assert all(e['y'] + e['h'] <= SAFE_BOTTOM for e in page['elements'])
    assert ('spotlight' in page) == (kind in ('concept', 'flow'))
    assert ('motion' in page) == (kind in ('comparison', 'exchange'))


def test_verbatim_anchors_become_measured_card_starts_and_first_card_opens_the_page():
    pytest.importorskip('PIL')
    value = design('concept', 3, end=1)
    nodes = value['sections'][0]['pages'][0]['nodes']
    nodes[0]['anchor'] = CAPTIONS[2][:4]  # 第一張卡一律從頁首開始，避免開頭空白。
    nodes[1]['anchor'] = CAPTIONS[1][2:8]
    nodes[2]['anchor'] = CAPTIONS[3]
    plan = compile_layout(value, [{}, {}], timeline(2, 4))
    # 第三句字幕與第二個 cue 同時開始，採用 cue 起點，不重複保存同一時間。
    assert steps(plan) == [(0, None), (0, 1), (1, 3)]


def test_unmatched_or_backward_anchors_fall_back_without_failing_the_page():
    pytest.importorskip('PIL')
    value = design('concept', 3, end=1)
    nodes = value['sections'][0]['pages'][0]['nodes']
    nodes[1]['anchor'] = '講稿裡沒有這句'
    nodes[2]['anchor'] = CAPTIONS[0][:5]  # 倒退到第一張卡之前，改用下一個實測起點。
    assert steps(compile_layout(value, [{}, {}], timeline(2, 4))) == [(0, None), (0, 1), (1, None)]
    # 太晚的 anchor 讓出位置給後面的卡，不讓整頁失敗。
    nodes[1]['anchor'], nodes[2]['anchor'] = CAPTIONS[3][:5], '講稿裡沒有這句'
    assert steps(compile_layout(value, [{}, {}], timeline(2, 4))) == [(0, None), (1, None), (1, 3)]


def test_too_few_measured_starts_share_a_card_instead_of_failing_the_video():
    pytest.importorskip('PIL')
    # 字幕只有段落粒度時，頁內實測起點可能少於節點；相鄰節點依序共用一張卡。
    plan = compile_layout(design('concept', 4), [{}], timeline(1, 3))
    page = plan['pages'][0]
    assert steps(plan) == [(0, None), (0, 1), (0, 2)]
    assert [len(s['rails']) for s in page['spotlight']['steps']] == [2, 1, 1]
    shared = [page['elements'][j]['text'] for j in page['spotlight']['steps'][0]['card'][1:]]
    assert shared == ['節點1', '來源支持的必要條件與說明。', '節點2', '來源支持的必要條件與說明。']
    layouts(plan)
    # 只有一個實測起點時，四個節點以兩欄小格共用一張卡，60 字說明仍放得下。
    single = design('concept', 4)
    for node in single['sections'][0]['pages'][0]['nodes']:
        node.update(label='標' * 8, text=('完整條件與必要說明，' * 6)[:60])
    shared = compile_layout(single, [{}], timeline(1, 1))
    assert [len(s['rails']) for s in shared['pages'][0]['spotlight']['steps']] == [4]
    layouts(shared)
    crowded = design('concept', 4)
    for node in crowded['sections'][0]['pages'][0]['nodes']:
        node['text'] = '完整條件與必要說明，' * 12
    with pytest.raises(ValueError, match='VIDEO_LAYOUT_INVALID'):
        compile_layout(crowded, [{}], timeline(1, 1))


def test_key_terms_are_checked_verbatim_and_marked_once_without_failing():
    pytest.importorskip('PIL')
    value = design('concept', 2)
    first, second = value['sections'][0]['pages'][0]['nodes']
    first.update(label='目標欄位', text='RFC 9213 引入 CDN-Cache-Control 標頭，CDN 會依它決定快取。',
                 key_terms=['CDN-Cache-Control 標頭', '標頭'])  # 第二個與第一個重疊，丟掉。
    second.update(label='HTTP 快取', key_terms=['不存在的詞', '來源支持的必要條件與說明。'])  # 不逐字／整句都丟掉。
    plan = compile_layout(value, [{}], timeline(1, 4))
    page = plan['pages'][0]
    marks = page['emphasis']
    assert [(m['quote'], page['elements'][m['element_index']]['text']) for m in marks] == \
        [('CDN-Cache-Control 標頭', first['text'])]
    assert marks[0]['kind'] == 'highlight' and marks[0]['end_cue'] == 0
    assert (marks[0]['start_cue'], marks[0].get('caption_index')) == steps(plan)[0]
    # 不在說明裡、但是標籤一部分的詞才標在標籤；整個標籤、太長或含空白邊界的詞丟掉。
    first['key_terms'] = ['目標', '目標欄位']
    second['key_terms'] = ['字' * (KEY_TERM_LIMIT + 1), ' HTTP']  # 中文字寬超過上限、前後空白都丟掉。
    page = compile_layout(value, [{}], timeline(1, 4))['pages'][0]
    assert [(m['quote'], page['elements'][m['element_index']]['text']) for m in page['emphasis']] == [('目標', '目標欄位')]
    first['key_terms'] = ['RFC', '標頭', 'CDN']  # 超過兩個只取前兩個，不讓整份失敗。
    second['key_terms'] = []
    page = compile_layout(value, [{}], timeline(1, 4))['pages'][0]
    assert [m['quote'] for m in page['emphasis']] == ['RFC', '標頭']
    # 單一字母與單獨數值即使逐字出現也丟掉；含數值的技術詞仍保留。
    first.update(text='損失 L 為 0.09，RFC 9213 定義此欄位。', key_terms=['L', '0.09'])
    second['key_terms'] = ['RFC 9213']
    second['text'] = '依 RFC 9213 的定義。'
    page = compile_layout(value, [{}], timeline(1, 4))['pages'][0]
    assert [m['quote'] for m in page['emphasis']] == ['RFC 9213']


def test_highlight_marks_only_the_first_occurrence_in_the_rendered_rows():
    pytest.importorskip('PIL')
    from runtime.podcast_video_render import highlight_segments
    value = design('concept', 1)
    node = value['sections'][0]['pages'][0]['nodes'][0]
    node.update(text='快取' * 25 + 'Content-Type 與 Content-Type', key_terms=['Content-Type'])
    plan = compile_layout(value, [{}], timeline(1, 4))
    mark = plan['pages'][0]['emphasis'][0]
    rows = layouts(plan)[0][mark['element_index']]
    segments = emphasis_layouts(plan, layouts(plan))[0][0]
    assert ''.join(rows[r][2][a:b] for r, a, b in segments) == 'Content-Type'
    assert sum(len(rows[r][2]) for r in range(segments[0][0])) + segments[0][1] == node['text'].index('Content-Type')
    # 斷行落在詞中時，同一個詞分段標在相鄰兩行，不改字級或斷行。
    assert highlight_segments([(0, 0, '前文Con'), (0, 0, 'tent-Type 後文')], '前文Content-Type 後文', 'Content-Type') == [(0, 2, 5), (1, 0, 9)]


def test_comparison_columns_stay_visible_and_focus_on_their_measured_anchor():
    pytest.importorskip('PIL')
    value = design('comparison', 2)
    value['sections'][0]['pages'][0]['nodes'][1]['anchor'] = CAPTIONS[2][1:6]
    plan = compile_layout(value, [{}], timeline(1, 4))
    page = plan['pages'][0]
    assert [(g['start_cue'], g.get('caption_index')) for g in page['motion']['groups']] == [(0, None), (0, 2)]
    assert page['reveal'] == []
    value['sections'][0]['pages'][0]['nodes'][1]['anchor'] = '講稿裡沒有這句'
    page = compile_layout(value, [{}], timeline(1, 4))['pages'][0]
    assert [g.get('caption_index') for g in page['motion']['groups']] == [None, None]


def test_exchange_preserves_packet_direction_and_deterministic_reveal_on_reverse_seek():
    pytest.importorskip('PIL')
    measured = timeline(1, 4)
    result = compile_layout(design(), [{}], measured)
    page = result['pages'][0]
    arrows = [e for e in page['elements'] if e['kind'] == 'arrow']
    assert [e['w'] > 0 for e in arrows] == [True, False, True, False]
    assert [g.get('caption_index') for g in page['reveal']] == [1, 2, 3]
    text = layouts(result)
    def board(t): return draw_frame(t, measured, result, text).crop((0, 220, 1920, 850)).tobytes()
    frames = [board(t) for t in (.5, 2.5, 4.5, 6.5)]
    assert len(set(frames)) == 4 and board(.5) == frames[0]


def test_exchange_messages_keep_spoken_order_when_an_anchor_is_unusable():
    pytest.importorskip('PIL')
    value = design()
    relations = value['sections'][0]['pages'][0]['relations']
    relations[1]['anchor'] = '講稿裡沒有這句'
    relations[2]['anchor'] = CAPTIONS[0][:5]
    page = compile_layout(value, [{}], timeline(1, 4))['pages'][0]
    times = [g.get('caption_index') for g in page['motion']['groups'][2:]]
    assert times == [None, None, None, 3]


@pytest.mark.parametrize('change', ['foreign', 'self', 'geometry', 'page', 'label', 'flow_relation', 'missing_anchor', 'legacy_timing'])
def test_semantic_contract_keeps_invalid_references_out_of_renderer(change):
    pytest.importorskip('PIL')
    value = design(); page = value['sections'][0]['pages'][0]
    if change == 'foreign': page['relations'][0]['target'] = 9
    elif change == 'self': page['relations'][0]['target'] = 0
    elif change == 'geometry': page['nodes'][0]['x'] = 100
    elif change == 'page': value['sections'][0]['start_cue'] = 1
    elif change == 'label': page['nodes'][0]['label'] = '標' * 33
    elif change == 'flow_relation':
        value = design('flow', 2); value['sections'][0]['pages'][0]['relations'] = [{'source': 0, 'target': 1, 'label': '', 'anchor': '第一句', 'key_terms': []}]
    elif change == 'missing_anchor':
        value = design('concept', 2); del value['sections'][0]['pages'][0]['nodes'][0]['anchor']
    else:
        value = design('concept', 2); value['sections'][0]['pages'][0]['nodes'][0].update(cue_index=0, reveal=True, focus=True)
    with pytest.raises(ValueError, match='VIDEO_STORYBOARD_INVALID'):
        compile_layout(value, [{}], timeline(1, 4))


def test_complete_technical_names_and_longer_sentences_fit_without_truncation():
    pytest.importorskip('PIL')
    value = design('concept', 4)
    node = value['sections'][0]['pages'][0]['nodes'][2]
    node.update(label='Content-Transfer-Encoding', text='此欄位描述傳輸編碼。')
    longer = value['sections'][0]['pages'][0]['nodes'][3]
    longer['text'] = '此欄位描述傳輸編碼，必須保留完整名稱與必要條件。' * 3
    assert 64 < len(longer['text']) <= 120
    compiled = compile_layout(value, [{}], timeline(1, 4))
    page = compiled['pages'][0]
    card = [page['elements'][j]['text'] for j in page['spotlight']['steps'][2]['card'][1:]]
    assert card == [node['label'], node['text']]
    assert page['elements'][page['spotlight']['steps'][2]['rails'][0]]['text'] == node['label']
    assert [page['elements'][j]['text'] for j in page['spotlight']['steps'][3]['card'][1:]][1] == longer['text']
    layouts(compiled)
    schema = semantic_schema(1)['properties']['sections']['items']['properties']['pages']['items']['anyOf'][0]['properties']['nodes']['items']['properties']
    assert schema['label']['maxLength'] >= len(node['label'])
    assert schema['text']['maxLength'] >= len(longer['text'])


def test_longer_contract_still_rejects_text_that_cannot_fit_its_area():
    pytest.importorskip('PIL')
    from runtime.podcast_video_layout import _element
    with pytest.raises(ValueError, match='VIDEO_LAYOUT_INVALID'):
        _element('完整文字。'*20,100,200,100,60,0)


def test_latin_key_terms_need_word_boundaries_and_do_not_split_technical_names():
    pytest.importorskip('PIL')
    from runtime.podcast_video_plan import term_at
    assert term_at('CDN-Cache-Control: max-age=60 套用於 CDN。', 'CDN') == 'CDN-Cache-Control: max-age=60 套用於 '.__len__()
    assert term_at('x_t 與 t，', 't') == 6 and term_at('max-age=60', 'age') == -1
    assert term_at('HTTP 快取與快取', '快取') == 5  # 中文詞不受詞邊界限制。
    value = design('concept', 2)
    first, second = value['sections'][0]['pages'][0]['nodes']
    first.update(text='CDN-Cache-Control: max-age=60 表示欄位套用於 CDN。', key_terms=['CDN', 'max-age=60'])
    second.update(text='只看 CDN-Cache-Control 標頭。', key_terms=['CDN', 'Cache'])  # 沒有合格位置就丟掉。
    plan = compile_layout(value, [{}], timeline(1, 4))
    page = plan['pages'][0]
    marks = [(m['quote'], page['elements'][m['element_index']]['text']) for m in page['emphasis']]
    assert marks == [('CDN', first['text']), ('max-age=60', first['text'])]
    rows = layouts(plan)[0][page['emphasis'][0]['element_index']]
    (row, a, b), = emphasis_layouts(plan, layouts(plan))[0][0]
    assert sum(len(rows[r][2]) for r in range(row)) + a == first['text'].rindex('CDN')


def split(kinds=('flow', 'concept'), anchors=('', CAPTIONS[1][:5]), cues=2, captions=4):
    """同一段講稿拆成多頁；第 2 頁起以逐字 anchor 決定換頁位置。"""
    pages = []
    for kind, anchor in zip(kinds, anchors):
        page = design(kind, 2)['sections'][0]['pages'][0]
        page['anchor'] = anchor
        pages.append(page)
    value = {'sections': [{'start_cue': 0, 'end_cue': cues - 1, 'pages': pages}]}
    return compile_layout(value, [{}] * cues, timeline(cues, captions)), timeline(cues, captions)


def windows(plan):
    return [(p['start_cue'], p.get('caption_index'), p['end_cue']) for p in plan['pages']]


def test_one_section_splits_into_pages_at_verbatim_anchors():
    pytest.importorskip('PIL')
    from runtime.podcast_video_plan import page_windows
    plan, measured = split()
    # 第二頁從第 2 句字幕開始，與第一頁共用同一個 teaching cue。
    assert windows(plan) == [(0, None, 0), (0, 1, 1)]
    assert page_windows(measured, plan) == [(0, 2), (2, 8)]
    assert 'spotlight' in plan['pages'][0] and 'spotlight' in plan['pages'][1]
    # 換頁點正好在 cue 開頭時，上一頁止於前一個 cue。
    plan, _ = split(anchors=('', CAPTIONS[2][:5]))
    assert windows(plan) == [(0, None, 0), (1, None, 1)]
    # 子頁的節點 anchor 落在前一頁範圍時退回子頁頁首，不跨頁。
    assert plan['pages'][1]['spotlight']['steps'][0]['start_cue'] == 1


@pytest.mark.parametrize('anchor', ['講稿裡沒有這句', CAPTIONS[0][:5], CAPTIONS[3][:5]])
def test_unusable_page_anchor_takes_the_middle_measured_start_without_failing(anchor):
    pytest.importorskip('PIL')
    # 對不到、等於段首或擠掉第三頁所需起點時，都改放在前後換頁點之間居中的實測起點。
    plan, _ = split(('concept', 'concept', 'concept'), ('', anchor, CAPTIONS[3][:5]), cues=2, captions=4)
    # 段內實測起點依序為 0 秒（cue 0）、2 秒（字幕 1）、4 秒（cue 1）、6 秒（字幕 3），居中取第 1 個。
    assert [w[:2] for w in windows(plan)] == [(0, None), (0, 1), (1, 3)]


def test_pages_without_their_own_measured_start_merge_into_the_previous_page():
    pytest.importorskip('PIL')
    # 無字幕、單 cue 的段只有一個實測起點：flow＋concept 兩頁併成一頁，節點都保留、沿用第一頁標題。
    plan, _ = split(anchors=('', '講稿裡沒有這句'), cues=1, captions=1)
    measured = timeline(1, 1); del measured['captions']
    value = {'sections': [{'start_cue': 0, 'end_cue': 0, 'pages': [
        dict(design('flow', 2)['sections'][0]['pages'][0], title='步驟'),
        dict(design('concept', 2)['sections'][0]['pages'][0], title='組成', anchor=CAPTIONS[0][:4])]}]}
    for page in (plan, compile_layout(value, [{}], measured)):
        assert len(page['pages']) == 1
        rails = page['pages'][0]['spotlight']['steps'][0]['rails']
        assert len(rails) == 4
    merged = compile_layout(value, [{}], measured)['pages'][0]
    assert merged['title'] == '步驟' and merged['spotlight']['layout'] == 'concept'
    # exchange 併入時保留訊息方向；併入後放不下才照規則回報版面錯誤。
    value['sections'][0]['pages'][1] = design()['sections'][0]['pages'][0]
    texts = [e['text'] for e in compile_layout(value, [{}], measured)['pages'][0]['elements']]
    assert '參與者1 → 參與者2' in texts
    for page in value['sections'][0]['pages']:
        page['layout'] = 'concept'; page['relations'] = []
        page['nodes'] = [dict(design('concept', 1)['sections'][0]['pages'][0]['nodes'][0], text='完整條件與必要說明，' * 12) for _ in range(6)]
    with pytest.raises(ValueError, match='VIDEO_LAYOUT_INVALID'):
        compile_layout(value, [{}], measured)


def test_exchange_needs_at_least_two_messages():
    pytest.importorskip('PIL')
    schema = semantic_schema(1)['properties']['sections']['items']['properties']['pages']['items']['anyOf'][3]
    assert schema['properties']['relations']['minItems'] == 2
    value = design(); value['sections'][0]['pages'][0]['relations'] = value['sections'][0]['pages'][0]['relations'][:1]
    with pytest.raises(ValueError, match='at least two ordered messages'):
        compile_layout(value, [{}], timeline(1, 4))


def test_section_page_limit_keeps_the_whole_storyboard_within_24_pages():
    from runtime.podcast_video_layout import section_page_limit
    from runtime.podcast_video_plan import V4_PAGE_LIMIT
    assert V4_PAGE_LIMIT == 24
    assert [section_page_limit(n) for n in (1, 8, 9, 12)] == [3, 3, 2, 2]
    assert all(n * section_page_limit(n) <= V4_PAGE_LIMIT for n in range(1, 13))
    schema = semantic_schema(12)['properties']['sections']['items']['properties']['pages']
    assert (schema['minItems'], schema['maxItems']) == (1, 2)


@pytest.mark.parametrize('kind', ['comparison', 'exchange'])
def test_comparison_and_exchange_subpages_anchor_to_their_own_page_start(kind):
    pytest.importorskip('PIL')
    from runtime.podcast_video_motion import scene_state
    from runtime.podcast_video_plan import page_windows
    plan, measured = split(('concept', kind), ('', CAPTIONS[1][:5]))
    page = plan['pages'][1]
    assert (page['start_cue'], page['caption_index']) == (0, 1)
    groups = page['motion']['groups']
    assert (groups[0]['start_cue'], groups[0].get('caption_index')) == (0, 1)
    bounds = page_windows(measured, plan)[1]
    assert scene_state(2, measured, page, bounds)[0]['entrance'] == 0 < scene_state(2.1, measured, page, bounds)[0]['entrance']


@pytest.mark.parametrize('change', ['first_page_caption', 'not_shared', 'anchor_before_page'])
def test_page_split_cannot_hide_content_outside_its_window(change):
    pytest.importorskip('PIL')
    from runtime.podcast_video_plan import validate_plan, validate_visual_timing
    plan, measured = split()
    if change == 'first_page_caption': plan['pages'][0]['caption_index'] = 0
    elif change == 'not_shared': plan['pages'][1]['start_cue'] = 1
    else: plan['pages'][1]['spotlight']['steps'][1]['caption_index'] = 0
    with pytest.raises(ValueError, match='VIDEO_STORYBOARD_INVALID'):
        validate_plan(plan, [{}, {}]); validate_visual_timing(plan, measured)

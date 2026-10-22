"""動態狀態與真實像素回歸；合成內容不作為教學品質驗收。"""
from copy import deepcopy
from hashlib import sha256
import pytest
from runtime.podcast_video_layout import compile_layout
from runtime.podcast_video_motion import scene_state, spotlight_state, transition
from runtime.podcast_video_plan import SAFE_BOTTOM, page_windows, validate_plan, validate_visual_timing
from runtime import podcast_video_render as render
from test_podcast_video_layout import CAPTIONS, design, timeline

pytest.importorskip('PIL')


def scene(kind='exchange', count=2):
    """exchange 四則訊息與 comparison 各欄都對到第 0～3 句字幕；聚焦頁每張卡一句。"""
    value = design(kind, count)
    if kind == 'comparison':
        value['sections'][0]['pages'][0]['nodes'][0]['key_terms'] = ['必要條件']
    measured = timeline(1, 4 if kind in ('exchange', 'comparison') else max(count, 4))
    return compile_layout(value, [{}], measured), measured


def board(plan, timeline, t):
    return render.draw_frame(t, timeline, plan, render.layouts(plan)).crop((0, 0, 1920, 852)).tobytes()


@pytest.mark.parametrize('kind', ['comparison', 'exchange'])
def test_entrance_is_continuous_and_preserves_existing_geometry(kind):
    plan, timeline = scene(kind); page = plan['pages'][0]
    initial = scene_state(0, timeline, page, page_windows(timeline, plan)[0]); partial = scene_state(.1, timeline, page, page_windows(timeline, plan)[0])
    assert initial[0]['entrance'] == 0 < partial[0]['entrance'] < 1
    assert scene_state(.5, timeline, page, page_windows(timeline, plan)[0])[0]['entrance'] == 1
    assert len({board(plan,timeline,t) for t in (0,.1,.5)}) == 3
    before = deepcopy(page)
    for t in (7,2.1,.1,4.3): board(plan,timeline,t)
    assert page == before


def test_comparison_lights_the_spoken_column_and_its_terms_never_leak_into_the_past():
    value = design('comparison', 2)
    value['sections'][0]['pages'][0]['nodes'][1].update(anchor=CAPTIONS[1][:5], key_terms=['必要條件'])
    timeline_ = timeline(1, 4)
    plan = compile_layout(value, [{}], timeline_); page = plan['pages'][0]
    assert scene_state(1.999, timeline_, page, page_windows(timeline_, plan)[0])[0]['focus'] == 1
    assert scene_state(1.999, timeline_, page, page_windows(timeline_, plan)[0])[1]['focus'] == 0
    partial = scene_state(2.1, timeline_, page, page_windows(timeline_, plan)[0])
    assert 0 < partial[0]['focus'] < 1 and 0 < partial[1]['focus'] < 1
    assert scene_state(2.5, timeline_, page, page_windows(timeline_, plan)[0])[0]['focus'] == 0
    assert scene_state(2.5, timeline_, page, page_windows(timeline_, plan)[0])[1]['focus'] == 1
    assert scene_state(7.9, timeline_, page, page_windows(timeline_, plan)[0])[0]['opacity'] == .65  # 持續可讀，不移除舊欄位。
    without = deepcopy(plan); without['pages'][0]['emphasis'] = []
    assert board(plan,timeline_,1.999) == board(without,timeline_,1.999)
    assert board(plan,timeline_,2.5) != board(without,timeline_,2.5)


def test_relation_draw_on_and_directed_progression_use_only_existing_arrow():
    plan, timeline = scene('exchange')
    page = plan['pages'][0]; group_index = 3
    group = page['motion']['groups'][group_index]
    start = 2
    before = scene_state(start-.001, timeline, page, page_windows(timeline, plan)[0])[group_index]
    first = scene_state(start+.15, timeline, page, page_windows(timeline, plan)[0])[group_index]
    last = scene_state(start+.5, timeline, page, page_windows(timeline, plan)[0])[group_index]
    done = scene_state(start+.8, timeline, page, page_windows(timeline, plan)[0])[group_index]
    assert before['draw'] == 0 and before['token'] is None
    assert 0 < first['draw'] < last['draw'] == 1
    assert 0 < first['token'] < last['token'] < 1 and done['token'] is None
    arrow = next(page['elements'][j] for j in group['elements'] if page['elements'][j]['kind'] == 'arrow')
    assert arrow['w'] < 0  # 回覆沿原本的反向箭頭移動。
    assert scene_state(start+.8, timeline, page, page_windows(timeline, plan)[0])[group['target']]['focus'] == 1


def spotlight(kind='concept', count=3):
    return scene(kind, count)


def test_spotlight_shows_one_card_at_a_time_and_marks_rail_progress():
    plan, timeline = spotlight()
    page = plan['pages'][0]
    assert [(s['now'], s['done']) for s in spotlight_state(1, timeline, page, page_windows(timeline, plan)[0])] == [(1, False), (0, False), (0, False)]
    crossing = spotlight_state(2.1, timeline, page, page_windows(timeline, plan)[0])
    assert 0 < crossing[0]['now'] < 1 and 0 < crossing[1]['now'] < 1 and crossing[0]['done']
    assert [(s['now'], s['done']) for s in spotlight_state(3, timeline, page, page_windows(timeline, plan)[0])] == [(0, True), (1, False), (0, False)]
    assert [(s['now'], s['done']) for s in spotlight_state(7.9, timeline, page, page_windows(timeline, plan)[0])] == [(0, True), (0, True), (1, False)]
    card = page['elements'][page['spotlight']['steps'][0]['card'][0]]
    area = (card['x'], card['y'], card['x'] + card['w'], card['y'] + card['h'])
    frames = {t: render.draw_frame(t, timeline, plan, render.layouts(plan)) for t in (1, 3, 5)}
    assert len({f.crop(area).tobytes() for f in frames.values()}) == 3
    # 章節列始終列出全部節點：講到的節點換成主色，其他保持中性灰。
    rail = page['elements'][page['spotlight']['steps'][1]['rails'][0]]
    row = (rail['x'], rail['y'], rail['x'] + rail['w'], rail['y'] + rail['h'])
    assert frames[1].crop(row).tobytes() != frames[3].crop(row).tobytes() != frames[5].crop(row).tobytes()


def test_spotlight_cards_share_one_area_but_overlap_is_still_checked_within_a_card():
    plan, _ = spotlight()
    render.layouts(plan)
    broken = deepcopy(plan); page = broken['pages'][0]
    label, body = page['spotlight']['steps'][0]['card'][1:]
    page['elements'][body]['y'] = page['elements'][label]['y']
    with pytest.raises(ValueError, match='VIDEO_LAYOUT_INVALID:.*text overlap'):
        render.layouts(broken)


def test_flow_rail_connects_steps_and_concept_rail_does_not():
    concept, timeline = spotlight('concept')
    flow, _ = spotlight('flow')
    def connector(plan):
        frame = render.draw_frame(1, timeline, plan, render.layouts(plan))
        page = plan['pages'][0]
        a, b = (page['elements'][s['rails'][0]] for s in page['spotlight']['steps'][:2])
        middle = round((a['y'] + a['h'] / 2 + b['y'] + b['h'] / 2) / 2)
        return frame.getpixel((132, middle))
    from PIL import ImageColor
    assert connector(flow) == ImageColor.getrgb(render.RAIL_LINE)
    assert connector(concept) == ImageColor.getrgb(render.BACKGROUND)


@pytest.mark.parametrize('kind', ['concept', 'comparison', 'flow', 'exchange'])
def test_subtitle_band_stays_empty_except_progress_bar(kind):
    from PIL import ImageColor
    plan, timeline = scene(kind, 3 if kind in ('concept', 'flow') else 2)
    background = ImageColor.getrgb(render.BACKGROUND)
    for t in (.1, 2.1, 3, 5.5, 7.9):
        band = render.draw_frame(t, timeline, plan, render.layouts(plan)).crop((0, SAFE_BOTTOM + 2, 1920, 1067))
        assert [color for _, color in band.getcolors()] == [background]
    broken = deepcopy(plan); lowest = max(broken['pages'][0]['elements'], key=lambda e: e['y'] + e['h'])
    lowest['h'] = SAFE_BOTTOM + 1 - lowest['y']
    with pytest.raises(ValueError, match='y=180..850'):
        validate_plan(broken, [{}])


def two_pages():
    value = design('concept', 1, end=0); other = deepcopy(value['sections'][0])
    other.update(start_cue=1, end_cue=1)
    other['pages'][0].update(title='下一個模型')
    other['pages'][0]['nodes'][0].update(label='另一個概念', anchor=CAPTIONS[1][:4])
    value['sections'].append(other)
    return compile_layout(value, [{}, {}], timeline(2, 2)), timeline(2, 2)


@pytest.mark.parametrize('kind', ['concept', 'comparison', 'flow', 'exchange', 'crossfade'])
@pytest.mark.parametrize('emphasis', [False, True])
def test_cached_frames_preserve_animation_progress_and_reverse_seek(kind, emphasis):
    plan, timeline = two_pages() if kind == 'crossfade' else scene(kind, 3 if kind in ('concept', 'flow') else 2)
    if emphasis:
        for page in plan['pages']:
            target = next(j for j, e in enumerate(page['elements']) if e['kind'] == 'text' and len(e['text']) >= 3)
            element = page['elements'][target]
            if element['text'].isascii():  # 英數訊息名稱不能被切開標色，改加一段中文說明再標。
                element['text'] += ' 訊息內容'
            page['emphasis'] = [{'element_index': target, 'start_cue': element['cue_index'],
                                 'end_cue': page['end_cue'], 'kind': 'highlight',
                                 'quote': '訊息' if element['text'].endswith('訊息內容') else element['text'][1:3],
                                 'color': 'orange'}]
            for item in page.get('spotlight', {}).get('steps', []) + page.get('motion', {}).get('groups', []):
                if target in item.get('card', item.get('elements', [])) and 'caption_index' in item:
                    page['emphasis'][0]['caption_index'] = item['caption_index']
        validate_plan(plan, [{}] * len(timeline['segments'])); validate_visual_timing(plan, timeline)
    else:
        for page in plan['pages']: page['emphasis'] = []
    positions = render.layouts(plan); marks = render.emphasis_layouts(plan, positions)
    times = [0, .1, .5, 1, 1.9, 2, 2.05, 2.1, 2.3, 2.5, 3, 3.9]
    cache = {}
    for t in times + list(reversed(times)) + times:
        expected = render.draw_frame(t, timeline, plan, positions, marks)
        actual = render.draw_frame(t, timeline, plan, positions, marks, scene_cache=cache)
        assert actual.tobytes() == expected.tobytes()
        assert len(cache) <= 2
    assert cache  # v4 highlight 也能重用靜止畫面，不必逐幀重畫。


def test_crossfade_is_short_and_has_no_future_page():
    import math
    plan, timeline = two_pages(); page = plan['pages'][1]
    assert transition(2,timeline,page,page_windows(timeline,plan)[1]) == 0
    assert 0 < transition(2.1,timeline,page,page_windows(timeline,plan)[1]) < 1
    assert transition(2.2,timeline,page,page_windows(timeline,plan)[1]) == 1
    assert board(plan,timeline,2) == board(plan,timeline,math.nextafter(2,-math.inf))
    assert len({board(plan,timeline,t) for t in (1.99,2.1,2.3)}) == 3


@pytest.mark.parametrize('rate', [1])
def test_repeat_render_backward_forward_seek_and_playback_rate_share_media_clock(rate):
    plan, timeline = spotlight()
    times = [0,.1,1.99,2,2.05,2.3,3.9,5.5]
    expected = {t:sha256(board(plan,timeline,t)).hexdigest() for t in times}
    for media_time in reversed(times):
        elapsed = media_time/rate
        assert sha256(board(plan,timeline,elapsed*rate)).hexdigest() == expected[media_time]
    for t in times: assert sha256(board(plan,timeline,t)).hexdigest() == expected[t]


@pytest.mark.parametrize('change',['duplicate','direction','raw_time','raw_path','missing','unknown_version','hidden_node','split_group','early_focus','foreign_caption','wrong_cue','offscreen','overflow'])
def test_motion_cannot_bypass_trusted_layout_or_measured_timing(change):
    plan, timeline = scene(); page = plan['pages'][0]; groups = page['motion']['groups']
    if change == 'duplicate': groups[1]['elements'].append(0)
    elif change == 'direction': groups[2].update(source=1, target=0)
    elif change == 'raw_time': groups[2]['duration'] = .5
    elif change == 'raw_path': groups[2]['path'] = [[0,0],[1,1]]
    elif change == 'missing': del page['motion']
    elif change == 'unknown_version': plan['schema'] = 'podcast-storyboard/v5'
    elif change == 'hidden_node': page['reveal'].append({'start_cue':0,'caption_index':3,'elements':groups[0]['elements']})
    elif change == 'split_group': page['reveal'].append({'start_cue':0,'caption_index':3,'elements':[groups[0]['elements'][1]]})
    elif change == 'early_focus': groups[3]['caption_index'] = 0
    elif change == 'foreign_caption': groups[3]['caption_index'] = 99
    elif change == 'wrong_cue': groups[3]['start_cue'] = 1
    elif change == 'offscreen': page['elements'][0]['x'] = 1900
    elif change == 'overflow': page['elements'][0]['text'] = '測'*160
    with pytest.raises(ValueError, match='VIDEO_(STORYBOARD|LAYOUT)_INVALID'):
        validate_plan(plan,[{}]);validate_visual_timing(plan,timeline);render.layouts(plan)


@pytest.mark.parametrize('change',['late_first','tied','foreign','missing_card','reveal','motion','extra'])
def test_spotlight_cannot_bypass_measured_order_or_one_card_rule(change):
    plan, timeline = spotlight(); page = plan['pages'][0]; steps = page['spotlight']['steps']
    if change == 'late_first': steps[0]['caption_index'] = 1
    elif change == 'tied': steps[2]['caption_index'] = steps[1]['caption_index']
    elif change == 'foreign': steps[1]['caption_index'] = 99
    elif change == 'missing_card': steps[1]['card'] = steps[1]['card'][:1]
    elif change == 'reveal': page['reveal'] = [{'start_cue':0,'elements':steps[0]['rails']}]
    elif change == 'motion': page['motion'] = {'layout':'concept','groups':[{'elements':[0],'start_cue':0}]}
    else: steps[0]['duration'] = 1
    with pytest.raises(ValueError, match='VIDEO_STORYBOARD_INVALID'):
        validate_plan(plan,[{}]);validate_visual_timing(plan,timeline)


def test_motion_render_still_checks_audio_hash_and_source_script(tmp_path):
    from test_podcast_video_plan import sample
    from runtime.podcast_video_plan import timeline_for_video
    episode,cues,alignment = sample();audio = tmp_path/'audio.wav';audio.write_bytes(b'synthetic')
    value = design('comparison',2,end=1)
    plan = compile_layout(value,cues,timeline_for_video('p',0,episode,cues,alignment,'/source'))
    request = {'podcast_id':'p','episode_index':0,'episode':episode,'cues':cues,'alignment':alignment,'source_resolver':'/source','plan':plan}
    with pytest.raises(ValueError,match='VIDEO_SOURCE_CHANGED'):render.render(request,audio,tmp_path/'no.mp4')
    episode['audio']['sha256'] = sha256(audio.read_bytes()).hexdigest()
    cues[0]['parts'][0]['text'] = '未授權替換講稿'
    with pytest.raises(ValueError,match='VIDEO_TRANSCRIPT_INVALID'):render.render(request,audio,tmp_path/'no.mp4')
    assert not (tmp_path/'no.mp4').exists()


def test_tied_anchors_do_not_invent_order_and_short_windows_finish_in_time():
    value = design()
    value['sections'][0]['pages'][0]['relations'][1]['anchor'] = CAPTIONS[0][:5]
    timeline_ = timeline(1, 4)
    plan = compile_layout(value,[{}],timeline_)
    state = scene_state(.1, timeline_, plan['pages'][0], page_windows(timeline_, plan)[0])
    assert state[2]['focus'] == state[3]['focus'] == 1
    assert state[2]['token'] == state[3]['token']
    for segment in timeline_['segments']:segment['end'] /= 100
    for caption in timeline_['captions']:
        caption['start'] /= 100;caption['end'] /= 100
    timeline_['duration'] /= 100
    plan = compile_layout(value,[{}],timeline_)
    states = scene_state(.019, timeline_, plan['pages'][0], page_windows(timeline_, plan)[0])
    assert states[2]['entrance'] == states[2]['draw'] == 1 and states[2]['token'] is None


def test_text_pixels_really_fade_and_dim_on_rgb_canvas():
    from PIL import ImageColor
    value = design('comparison', 2); value['sections'][0]['pages'][0]['nodes'][1]['anchor'] = CAPTIONS[1][:5]
    timeline_ = timeline(1, 4)
    plan = compile_layout(value, [{}], timeline_); e = plan['pages'][0]['elements'][0]
    positions = render.layouts(plan)
    frames = [render.draw_frame(t,timeline_,plan,positions) for t in (.05,.2,.5,2.5)]
    area = (e['x'],e['y'],e['x']+e['w'],e['y']+e['h'])
    rgb = ImageColor.getrgb(render.PALETTE[e['color']])
    full = frames[2].crop(area)
    # 找完全覆蓋的字形內部，避免僅驗證背景／框線的透明度。
    point = next((x,y) for y in range(0,e['h']) for x in range(0,100) if full.getpixel((x,y)) == rgb)
    colors = [frame.getpixel((e['x']+point[0],e['y']+point[1])) for frame in frames]
    assert sum(colors[0]) > sum(colors[1]) > sum(colors[2])
    assert sum(colors[3]) > sum(colors[2]) and sum(colors[3]) < sum(colors[0])


def test_highlight_keeps_size_and_wrapping_and_only_recolors_the_term():
    from PIL import ImageColor
    value = design('concept', 1)
    value['sections'][0]['pages'][0]['nodes'][0].update(text='經過 CDN 的回應會依 CDN-Cache-Control 決定快取。', key_terms=['CDN-Cache-Control'])
    timeline_ = timeline(1, 4)
    plan = compile_layout(value, [{}], timeline_)
    plain = deepcopy(plan); plain['pages'][0]['emphasis'] = []
    assert render.layouts(plan) == render.layouts(plain)
    mark = plan['pages'][0]['emphasis'][0]
    (row, a, b), = render.emphasis_layouts(plan, render.layouts(plan))[0][0]
    x, y, text = render.layouts(plan)[0][mark['element_index']][row]
    size = plan['pages'][0]['elements'][mark['element_index']]['size']
    term = (round(x + render.font(size).getlength(text[:a])), round(y), round(x + render.font(size).getlength(text[:b])), round(y + size * 1.3))
    prefix = (round(x), round(y), term[0] - 6, term[3])
    marked = render.draw_frame(1, timeline_, plan, render.layouts(plan))
    unmarked = render.draw_frame(1, timeline_, plain, render.layouts(plain))
    assert marked.crop(prefix).tobytes() == unmarked.crop(prefix).tobytes()
    assert marked.crop(term).tobytes() != unmarked.crop(term).tobytes()
    assert ImageColor.getrgb(render.PALETTE['teal']) in {color for _, color in marked.crop(term).getcolors(1 << 16)}


def test_renderer_cli_compiles_v4_storyboard(tmp_path):
    import json,subprocess,sys
    from test_podcast_video_plan import sample
    episode,cues,alignment = sample()
    request = {'podcast_id':'p','episode_index':0,'episode':episode,'cues':cues,'alignment':alignment,
               'source_resolver':'/source','semantic_plan':design('comparison',2,end=1)}
    source = tmp_path/'request.json';result = tmp_path/'result.json';source.write_text(json.dumps(request))
    completed = subprocess.run([sys.executable,'-m','runtime.podcast_video_render',str(source),str(tmp_path/'unused.wav'),str(result),'--validate'],capture_output=True,timeout=10)
    assert completed.returncode == 0
    compiled = json.loads(result.read_text())
    assert compiled['valid'] and compiled['plan']['schema'] == 'podcast-storyboard/v4'
    assert 'motion' in compiled['plan']['pages'][0]


def test_first_delayed_focus_smoothly_leaves_the_page_preview():
    value = design('comparison',2,end=1)
    value['sections'][0]['pages'][0]['nodes'][0]['anchor'] = CAPTIONS[2][:5]
    value['sections'][0]['pages'][0]['nodes'][1]['anchor'] = CAPTIONS[3][:5]
    timeline_ = timeline(2, 4)
    plan = compile_layout(value,[{},{}],timeline_);page = plan['pages'][0]
    assert scene_state(3.9, timeline_, page, page_windows(timeline_, plan)[0])[1]['focus'] == 1
    assert scene_state(4, timeline_, page, page_windows(timeline_, plan)[0])[1]['focus'] == 1
    assert 0 < scene_state(4.1, timeline_, page, page_windows(timeline_, plan)[0])[1]['focus'] < 1
    assert scene_state(4.4, timeline_, page, page_windows(timeline_, plan)[0])[1]['focus'] == 0


def test_highlight_cannot_appear_before_its_card():
    value = design('concept', 2)
    value['sections'][0]['pages'][0]['nodes'][1]['key_terms'] = ['必要條件']
    plan, timeline_ = compile_layout(value, [{}], timeline(1, 4)), timeline(1, 4)
    mark = plan['pages'][0]['emphasis'][0]
    assert mark['caption_index'] == 1
    validate_visual_timing(plan, timeline_)
    del mark['caption_index']  # 退回頁首會早於第二張卡出現。
    with pytest.raises(ValueError, match='emphasis must not precede'):
        validate_visual_timing(plan, timeline_)


def test_subpage_counter_selection_and_crossfade_follow_the_split_caption():
    from test_podcast_video_layout import split
    plan, measured = split()
    first, second = (render.draw_frame(t, measured, plan, render.layouts(plan)) for t in (1.99, 2.5))
    # 同一個 cue 內，2 秒的字幕開頭就換到第二頁：頁碼由 01 / 02 變成 02 / 02，並短暫交叉淡化。
    assert first.crop((0, 150, 1920, 852)).tobytes() != second.crop((0, 150, 1920, 852)).tobytes()
    assert 0 < transition(2.1, measured, plan['pages'][1], (2, 8)) < 1 == transition(2.25, measured, plan['pages'][1], (2, 8))
    counter = (1680, 60, 1800, 110)
    assert first.crop(counter).tobytes() != second.crop(counter).tobytes()

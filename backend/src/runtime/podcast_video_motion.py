"""v3 的有限動態語法：只使用編譯後群組、既有箭頭與實測時間，不累積影格狀態。"""
import math
from .podcast_video_plan import visual_window


def ease(value):
    value = min(1., max(0., value))
    return value * value * (3 - 2 * value)


def window(item, timeline):
    return visual_window(timeline, item['start_cue'], item['start_cue'], item.get('caption_index'))


def progress(t, start, end, maximum):
    # 短字幕也必須在自己的實測區間內完成；固定上限是視覺節奏，並非教材中的速度。
    return ease((t - start) / min(maximum, (end - start) / 3))


def validate_motion(page, where):
    motion = page['motion']
    if (not isinstance(motion, dict) or set(motion) != {'layout', 'groups'}
            or motion['layout'] not in ('concept', 'comparison', 'flow', 'exchange', 'interaction')
            or not isinstance(motion['groups'], list) or not 1 <= len(motion['groups']) <= 12
            or any(not isinstance(group, dict) for group in motion['groups'])):
        raise ValueError(where + ', invalid motion groups')
    groups = motion['groups']; used = []
    nodes = [i for i, g in enumerate(groups) if isinstance(g, dict) and 'source' not in g]
    if motion['layout'] == 'interaction' and (len(nodes) != 2 or len(groups) <= 2):
        raise ValueError(where + ', interaction requires two actors and at least one message')
    for i, group in enumerate(groups):
        fields = {'elements', 'start_cue'} | ({'source', 'target'} if 'source' in group else set())
        if motion['layout'] == 'interaction':
            fields.add('outcome' if 'source' in group else 'icon')
            if ('source' in group and group.get('outcome') not in ('delivered', 'lost')
                    or 'source' not in group and group.get('icon') not in ('computer', 'server', 'mailbox', 'process')):
                raise ValueError(where + ', invalid interaction actor or outcome')
        if (not isinstance(group, dict) or set(group) not in (fields, fields | {'caption_index'})
                or type(group['start_cue']) is not int or not page['start_cue'] <= group['start_cue'] <= page['end_cue']
                or ('caption_index' in group and (type(group['caption_index']) is not int or group['caption_index'] < 0))
                or not isinstance(group['elements'], list) or not group['elements']
                or any(type(j) is not int or not 0 <= j < len(page['elements']) for j in group['elements'])):
            raise ValueError(where + ', invalid motion anchor or element')
        used.extend(group['elements'])
        elements = [page['elements'][j] for j in group['elements']]
        if any(e['cue_index'] != group['start_cue'] for e in elements):
            raise ValueError(where + ', motion must follow the element explanation')
    for group in groups:
        elements = [page['elements'][j] for j in group['elements']]
        if 'source' not in group:
            if (elements[0]['kind'] not in ('text', 'box', 'circle')
                    or any(e['kind'] != 'line' for e in elements[1:])):
                raise ValueError(where + ', motion node must contain a labelled shape and optional guides')
            continue
        if (motion['layout'] not in ('flow', 'exchange', 'interaction')
                or any(type(group[k]) is not int or group[k] not in nodes for k in ('source', 'target'))
                or group['source'] == group['target']
                or (motion['layout'] == 'flow' and group['target'] != group['source'] + 1)
                or sum(e['kind'] == 'arrow' for e in elements) != 1
                or any(e['kind'] not in ('text', 'arrow') for e in elements)):
            raise ValueError(where + ', motion requires an existing directed relation')
        arrow = next(e for e in elements if e['kind'] == 'arrow')
        for key, point in (('source', (arrow['x'], arrow['y'])),
                           ('target', (arrow['x'] + arrow['w'], arrow['y'] + arrow['h']))):
            # 僅核對 v3 動態綁定；舊平面分鏡的合法留白不受此限制。
            target = [page['elements'][j] for j in groups[group[key]]['elements']]
            def distance(e):
                if e['kind'] == 'line':
                    a = (e['x'], e['y'])
                    ratio = min(1, max(0, ((point[0]-a[0])*e['w'] + (point[1]-a[1])*e['h']) / (e['w']**2 + e['h']**2)))
                    return math.dist(point, (a[0]+ratio*e['w'], a[1]+ratio*e['h']))
                return math.hypot(max(e['x']-point[0], 0, point[0]-e['x']-e['w']),
                                  max(e['y']-point[1], 0, point[1]-e['y']-e['h']))
            if min(map(distance, target)) > 24:
                raise ValueError(where + ', motion direction must match the existing arrow endpoints')
    if sorted(used) != list(range(len(page['elements']))):
        raise ValueError(where + ', motion must group every element exactly once')


def validate_motion_timing(page, timeline, visible, where):
    if page['motion']['layout'] == 'interaction':
        starts = [window(g, timeline)[0] for g in page['motion']['groups'] if 'source' in g]
        if any(a >= b for a, b in zip(starts, starts[1:])):
            raise ValueError(where + ', interaction messages require distinct ordered measured anchors; use exchange for simultaneous statements')
    for group in page['motion']['groups']:
        if page['motion']['layout'] == 'interaction' and 'source' not in group:
            if window(group, timeline)[0] != timeline['segments'][page['start_cue']]['start'] or any(visible[j] != window(group, timeline)[0] for j in group['elements']):
                raise ValueError(where + ', interaction actors must be visible from page start')
        times = {visible[j] for j in group['elements']}
        if len(times) != 1 or next(iter(times)) > window(group, timeline)[0]:
            raise ValueError(where + ', motion focus must follow its whole revealed group')
        if 'source' in group:
            for key in ('source', 'target'):
                if any(visible[j] > min(times) for j in page['motion']['groups'][group[key]]['elements']):
                    raise ValueError(where + ', motion relation must not precede its nodes')


def scene_state(t, timeline, page):
    """同時的語意 anchor 同時聚焦；不替模型杜撰額外的教學先後。"""
    groups = page['motion']['groups']
    page_start = timeline['segments'][page['start_cue']]['start']
    appearances = {j: (page_start, timeline['segments'][page['start_cue']]['end'])
                   for j in range(len(page['elements']))}
    for reveal in page.get('reveal', []):
        for j in reveal['elements']: appearances[j] = window(reveal, timeline)
    anchors = [window(g, timeline) for g in groups]
    event_times = sorted({start for start, _ in anchors})
    # 整 cue 焦點也要在下一個細 anchor 前完成轉移，避免短字幕邊界跳動。
    anchors = [(start, min(end, next((later for later in event_times if later > start), end)))
               for start, end in anchors]
    events = [start for start in event_times if start <= t]

    def weights(event, now):
        values = [0.] * len(groups)
        for i, (start, end) in enumerate(anchors):
            if start != event: continue
            values[i] = 1.
            if 'source' in groups[i]:
                phase = progress(now, start, end, .7)
                for key, weight in (('source', 1-phase), ('target', phase)):
                    target = groups[i][key]; values[target] = max(values[target], weight)
        return values

    focus = [1.] * len(groups)
    if events:
        latest = events[-1]; current = weights(latest, t)
        previous = weights(events[-2], latest) if len(events) > 1 else ([1.] * len(groups) if latest > page_start else current)
        end = min(end for start, end in anchors if start == latest)
        blend = progress(t, latest, end, .28)
        focus = [a + (b-a)*blend for a, b in zip(previous, current)]
    result = []
    for i, group in enumerate(groups):
        start, end = appearances[group['elements'][0]]
        anchor, stop = anchors[i]
        if start == anchor: end = min(end, stop)
        entrance = progress(t, start, end, .4)
        phase = progress(t, anchor, stop, .7)
        result.append({'entrance': entrance, 'focus': focus[i],
                       'opacity': entrance * (.65 + .35*focus[i]),
                       'draw': progress(t, start, end, .5),
                       'token': phase if 'source' in group and anchor < t < stop and phase < 1 else None})
    return result


def transition(t, timeline, page):
    start = timeline['segments'][page['start_cue']]['start']
    end = timeline['segments'][page['end_cue']]['end']
    return progress(t, start, end, .2)

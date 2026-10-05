"""語意分鏡編譯成有限圖形與可選 v3 動態群組；座標、留白與文字容量由可信程式決定。"""
from .podcast_video_plan import object_schema, validate_plan, validate_visual_timing, visual_window


def semantic_schema(cues, caption_count=0):
    cue = {'type': 'integer', 'enum': list(range(len(cues)))}
    intent = {'cue_index': cue, 'reveal': {'type': 'boolean'}, 'focus': {'type': 'boolean'},
              'caption_index': {'type': ['integer', 'null'], 'enum': [None, *range(caption_count)]}}
    variants = []
    for layout in ('concept', 'comparison', 'flow', 'exchange'):
        node = object_schema({'label': {'type': 'string', 'minLength': 1, 'maxLength': 16, 'pattern': r'^[^\r\n]*$'},
                              'text': {'type': 'string', 'enum': ['']} if layout == 'exchange' else
                                      {'type': 'string', 'maxLength': 64, 'pattern': r'^[^\r\n]*$'}, **intent})
        relation = object_schema({'source': {'type': 'integer', 'minimum': 0, 'maximum': 5},
                                  'target': {'type': 'integer', 'minimum': 0, 'maximum': 5},
                                  'label': {'type': 'string', 'minLength': 1, 'maxLength': 36, 'pattern': r'^[^\r\n]*$'} if layout == 'exchange' else
                                           {'type': 'string', 'enum': ['']}, **intent})
        low, high = (2, 2) if layout == 'exchange' else (2, 3) if layout == 'comparison' else (1, 6)
        variants.append(object_schema({'layout': {'type': 'string', 'enum': [layout]},
            'title': {'type': 'string', 'minLength': 1, 'maxLength': 28}, 'start_cue': cue, 'end_cue': cue,
            'nodes': {'type': 'array', 'minItems': low, 'maxItems': high, 'items': node},
            'relations': {'type': 'array', 'minItems': 1 if layout == 'exchange' else 0,
                          'maxItems': 6 if layout in ('flow', 'exchange') else 0, 'items': relation}}))
    return object_schema({'pages': {'type': 'array', 'minItems': 1, 'maxItems': 12, 'items': {'anyOf': variants}}})


def _element(text, x, y, w, h, cue, color='ink', kind='text', maximum=42):
    from .podcast_video_render import lines
    padding = 32 if kind == 'box' else 0
    size = next((s for s in (64, 52, 42, 34, 28) if s <= maximum
                 and len(lines(text, s, w-padding)) * (s+10) <= h-padding), None)
    if size is None:
        raise ValueError('VIDEO_LAYOUT_INVALID:node text exceeds its reading area; shorten the label or explanation without dropping necessary conditions')
    return {'kind': kind, 'cue_index': cue, 'text': text, 'x': x, 'y': y, 'w': w, 'h': h,
            'size': size, 'color': color, 'filled': False}


def _line(x, y, w, h, cue, color='muted', kind='line'):
    return {'kind': kind, 'cue_index': cue, 'text': '', 'x': x, 'y': y, 'w': w, 'h': h,
            'size': 28, 'color': color, 'filled': False}


def compile_layout(design, cues, timeline=None, *, motion=False):
    """只編排模型明示的節點／關係，不自行新增箭頭、推論或文字。"""
    try:
        if set(design) != {'pages'} or not 1 <= len(design['pages']) <= 12:
            raise ValueError('VIDEO_STORYBOARD_INVALID:expected 1..12 semantic pages')
        pages = []; next_cue = 0
        for p, spec in enumerate(design['pages']):
            where = f'VIDEO_STORYBOARD_INVALID:page={p}'
            if set(spec) != {'layout', 'title', 'start_cue', 'end_cue', 'nodes', 'relations'}:
                raise ValueError(where + ', invalid semantic page fields')
            start, end = spec['start_cue'], spec['end_cue']; kind = spec['layout']
            nodes, relations = spec['nodes'], spec['relations']
            if (type(start) is not int or type(end) is not int or start != next_cue or not start <= end < len(cues)
                    or kind not in ('concept', 'comparison', 'flow', 'exchange')
                    or not isinstance(nodes, list) or not 1 <= len(nodes) <= 6
                    or not isinstance(relations, list) or len(relations) > 6):
                raise ValueError(where + ', invalid page range, layout or item count')
            if kind == 'exchange' and (len(nodes) != 2 or not relations):
                raise ValueError(where + ', exchange requires two participants and ordered messages')
            if kind == 'comparison' and not 2 <= len(nodes) <= 3:
                raise ValueError(where + ', comparison requires two or three groups')
            if kind in ('concept', 'comparison') and relations:
                raise ValueError(where + ', use flow or exchange for supported directed relations')
            for i, node in enumerate(nodes):
                if (set(node) not in ({'label', 'text', 'cue_index', 'reveal', 'focus'}, {'label', 'text', 'cue_index', 'reveal', 'focus', 'caption_index'})
                        or not isinstance(node['label'], str) or not 1 <= len(node['label']) <= 16
                        or not isinstance(node['text'], str) or len(node['text']) > 64
                        or '\n' in node['label'] + node['text'] or '\r' in node['label'] + node['text']
                        or (kind == 'exchange' and node['text'])):
                    raise ValueError(where + f', node={i}: provide a short label and explanation; exchange participants use empty text')
            for i, relation in enumerate(relations):
                if (set(relation) not in ({'source', 'target', 'label', 'cue_index', 'reveal', 'focus'}, {'source', 'target', 'label', 'cue_index', 'reveal', 'focus', 'caption_index'})
                        or any(type(relation[k]) is not int or not 0 <= relation[k] < len(nodes) for k in ('source', 'target'))
                        or relation['source'] == relation['target']
                        or not isinstance(relation['label'], str) or len(relation['label']) > 36
                        or '\n' in relation['label'] or '\r' in relation['label']):
                    raise ValueError(where + f', relation={i}: invalid participant or label')
                if kind == 'flow' and (relation['target'] != relation['source'] + 1 or relation['label']):
                    raise ValueError(where + f', relation={i}: flow connects adjacent ordered steps; put explanations in the step nodes')
                if kind == 'exchange' and not relation['label']:
                    raise ValueError(where + f', relation={i}: message needs its source-supported label')
            for item in nodes + relations:
                if (type(item['cue_index']) is not int or not start <= item['cue_index'] <= end
                        or type(item['reveal']) is not bool or type(item['focus']) is not bool):
                    raise ValueError(where + ', cue_index must be within the page; reveal/focus must be boolean')
                caption = item.get('caption_index')
                if caption is not None:
                    if timeline is None or type(caption) is not int or not 0 <= caption < len(timeline.get('captions', [])):
                        raise ValueError(where + ', caption_index has no measured anchor')
                    cue = timeline['segments'][item['cue_index']]
                    if not cue['start'] <= timeline['captions'][caption]['start'] < cue['end']:
                        raise ValueError(where + ', caption_index must belong to cue_index; use null for whole-cue timing')
            if kind == 'exchange' and any(a['cue_index'] > b['cue_index'] for a, b in zip(relations, relations[1:])):
                raise ValueError(where + ', exchange messages must follow the spoken sequence')
            page = {'title': spec['title'], 'start_cue': start, 'end_cue': end,
                    'elements': [], 'emphasis': [], 'reveal': []}
            elements = page['elements']; visible = []; node_bounds = []; motion_groups = []
            def group(item, first, **relation):
                motion_groups.append({'elements': list(range(first, len(elements))), 'start_cue': item['cue_index'],
                    **({'caption_index': item['caption_index']} if item.get('caption_index') is not None else {}), **relation})
            def appearance(item):
                return (item['cue_index'], item.get('caption_index')) if item['reveal'] else (start, None)

            def time_at(when):
                return visual_window(timeline, when[0], when[0], when[1])[0] if timeline else when[0]

            node_starts = [appearance(n) for n in nodes]
            for edge in relations:
                when = appearance(edge)
                for target in (edge['source'], edge['target']):
                    node_starts[target] = min(node_starts[target], when, key=time_at)

            def add(element, when, focus=False, mark='outline', caption=None):
                index = len(elements); elements.append(element); visible.append(when)
                if focus:
                    page['emphasis'].append({'element_index': index, 'start_cue': element['cue_index'],
                        'end_cue': element['cue_index'], 'kind': mark, 'quote': '',
                        **({'caption_index': caption} if caption is not None else {})})
                return index

            for i, node in enumerate(nodes):
                first = len(elements)
                color = ('teal', 'blue', 'orange')[i % 3]
                text = node['label'] + ('\n' + node['text'] if node['text'] else '')
                if kind == 'exchange':
                    x, y, w, h = 140 + i*1140, 230, 500, 100
                    shape, size = 'box', 42
                elif kind == 'comparison':
                    w = (1720 - (len(nodes)-1)*70) // len(nodes)
                    x, y, h = 100 + i*(w+70), 310, 400
                    shape, size = 'text', 52
                elif kind == 'concept' and len(nodes) <= 4:
                    if len(nodes) == 1:
                        x, y, w, h = 180, 300, 1560, 380
                    elif i == 0:
                        x, y, w, h = 120, 310, 670, 380
                    else:
                        h = 510 // (len(nodes)-1)
                        x, y, w = 930, 250 + (i-1)*h, 850
                        h -= 16
                    shape, size = 'text', 52 if i == 0 else 42
                else:
                    columns = min(3, len(nodes)); rows = (len(nodes)+2)//3
                    row, col = divmod(i, columns)
                    if row % 2: col = columns-1-col
                    w = (1720-(columns-1)*100)//columns
                    x = 100 + col*(w+100); y = 250 + row*310 if rows > 1 else 370
                    h = 224 if rows > 1 else 300
                    shape, size = 'box' if kind == 'flow' else 'text', 42
                add(_element(text, x, y, w, h, node['cue_index'], color, shape, size), node_starts[i], node['focus'], caption=node.get('caption_index'))
                node_bounds.append((x, y, w, h))
                if kind == 'comparison':
                    add(_line(x, 265, w, 0, node['cue_index'], color), node_starts[i])
                if kind == 'exchange':
                    add(_line(x+w//2, 340, 0, 440, node['cue_index']), node_starts[i])
                group(node, first)
            for i, edge in enumerate(relations):
                first = len(elements)
                when = appearance(edge)
                a, b = node_bounds[edge['source']], node_bounds[edge['target']]
                if kind == 'exchange':
                    x1, x2 = a[0]+a[2]//2, b[0]+b[2]//2
                    y = 410 + i*(340//max(1, len(relations)-1))
                    add(_element(edge['label'], 420, y-47, 1080, 42, edge['cue_index'], 'ink', maximum=34), when)
                    arrow = _line(x1, y, x2-x1, 0, edge['cue_index'], 'ink', 'arrow')
                elif a[1] == b[1]:
                    direction = 1 if a[0] < b[0] else -1
                    x1 = a[0]+a[2]+12 if direction == 1 else a[0]-12
                    x2 = b[0]-12 if direction == 1 else b[0]+b[2]+12
                    arrow = _line(x1, a[1]+a[3]//2, x2-x1, 0, edge['cue_index'], 'ink', 'arrow')
                else:
                    x = a[0]+a[2]//2; y = a[1]+a[3]+12
                    arrow = _line(x, y, 0, b[1]-12-y, edge['cue_index'], 'ink', 'arrow')
                add(arrow, when, edge['focus'], 'trace', edge.get('caption_index'))
                group(edge, first, source=edge['source'], target=edge['target'])
            groups = {}
            for i, when in enumerate(visible):
                if time_at(when) > time_at((start, None)): groups.setdefault(when, []).append(i)
            page['reveal'] = [{'start_cue': cue, 'elements': targets, **({'caption_index': caption} if caption is not None else {})}
                              for (cue, caption), targets in sorted(groups.items(), key=lambda pair: time_at(pair[0]))]
            if motion: page['motion'] = {'layout': kind, 'groups': motion_groups}
            pages.append(page); next_cue = end+1
        result = validate_plan({'schema': 'podcast-storyboard/v3' if motion else 'podcast-storyboard/v2', 'pages': pages}, cues)
        if timeline is not None: validate_visual_timing(result, timeline)
        return result
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError('VIDEO_STORYBOARD_INVALID:invalid semantic layout fields') from error

"""語意分鏡編譯成既有 v2 圖形；座標、留白與文字容量由可信程式決定。"""
from .podcast_video_plan import object_schema, validate_plan


def semantic_schema(cues):
    cue = {'type': 'integer', 'enum': list(range(len(cues)))}
    intent = {'cue_index': cue, 'reveal': {'type': 'boolean'}, 'focus': {'type': 'boolean'}}
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


def compile_layout(design, cues):
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
                if (set(node) != {'label', 'text', 'cue_index', 'reveal', 'focus'}
                        or not isinstance(node['label'], str) or not 1 <= len(node['label']) <= 16
                        or not isinstance(node['text'], str) or len(node['text']) > 64
                        or '\n' in node['label'] + node['text'] or '\r' in node['label'] + node['text']
                        or (kind == 'exchange' and node['text'])):
                    raise ValueError(where + f', node={i}: provide a short label and explanation; exchange participants use empty text')
            for i, relation in enumerate(relations):
                if (set(relation) != {'source', 'target', 'label', 'cue_index', 'reveal', 'focus'}
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
            if kind == 'exchange' and any(a['cue_index'] > b['cue_index'] for a, b in zip(relations, relations[1:])):
                raise ValueError(where + ', exchange messages must follow the spoken sequence')
            page = {'title': spec['title'], 'start_cue': start, 'end_cue': end,
                    'elements': [], 'emphasis': [], 'reveal': []}
            elements = page['elements']; visible = []; node_bounds = []
            node_starts = [n['cue_index'] if n['reveal'] else start for n in nodes]
            for edge in relations:
                when = edge['cue_index'] if edge['reveal'] else start
                for target in (edge['source'], edge['target']):
                    node_starts[target] = min(node_starts[target], when)

            def add(element, when, focus=False, mark='outline'):
                index = len(elements); elements.append(element); visible.append(when)
                if focus:
                    page['emphasis'].append({'element_index': index, 'start_cue': element['cue_index'],
                        'end_cue': element['cue_index'], 'kind': mark, 'quote': ''})
                return index

            for i, node in enumerate(nodes):
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
                add(_element(text, x, y, w, h, node['cue_index'], color, shape, size), node_starts[i], node['focus'])
                node_bounds.append((x, y, w, h))
                if kind == 'comparison':
                    add(_line(x, 265, w, 0, node['cue_index'], color), node_starts[i])
                if kind == 'exchange':
                    add(_line(x+w//2, 340, 0, 440, node['cue_index']), node_starts[i])
            for i, edge in enumerate(relations):
                when = edge['cue_index'] if edge['reveal'] else start
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
                add(arrow, when, edge['focus'], 'trace')
            groups = {}
            for i, when in enumerate(visible):
                if when > start: groups.setdefault(when, []).append(i)
            page['reveal'] = [{'start_cue': cue, 'elements': targets} for cue, targets in sorted(groups.items())]
            pages.append(page); next_cue = end+1
        return validate_plan({'schema': 'podcast-storyboard/v2', 'pages': pages}, cues)
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError('VIDEO_STORYBOARD_INVALID:invalid semantic layout fields') from error

"""只補正尚未可讀的學習文字，保留技術片段與來源；結果仍須通過教材審查。"""
from copy import deepcopy
import re

from .claim_literals import mask_literals, restore_literals
from .semantic_projection import _expand_claim, _project_claim

POLICY = 'traditional-chinese-literals/v1'


def _chinese(text):
    return isinstance(text, str) and re.search(r'[\u3400-\u9fff]', text) is not None


def _needs_translation(text, sources=()):
    if not _chinese(text):
        return True
    prose, _ = mask_literals(text)
    if re.search(r'[\u0400-\u1cff\u3040-\u30ff\uac00-\ud7af]|[\u0370-\u03ff]{4,}', prose):
        return True
    # PDF 的獨立圖解標籤可能被串成「un believ able」。只接受原引用中
    # 各自獨立的單字標題；一般英文敘述、未引用的標籤仍須翻譯。
    labels = {
        value.casefold() for source in sources
        if source.get('kind') == 'heading'
        for value in [source.get('exact_text', source.get('quote', '')).strip()]
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', value)
    }
    return any(
        not all(word in labels for word in match[0].split())
        for match in re.finditer(r'\b[a-z]+(?:[ -]+[a-z]+){2,}\b', prose)
    )


def localize_block(response, context, bundle, call):
    result = deepcopy(response)
    items, targets = {}, {}
    evidence = {e['evidence_id']: e for e in bundle['evidence']}

    def add(key, text, target, field, *, maximum=4096, sources=None):
        if sources:
            parts, literals = [], {}
            for source in sources:
                part, protected = mask_literals(source['exact_text'], source['kind'], token_start=len(literals))
                parts.append(part)
                literals.update(protected)
            masked = '\n\n'.join(parts)
        else:
            masked, literals = mask_literals(text)
        items[key] = {'text': masked, 'literals': literals,
                      'kind': '概念名稱' if field == 'l' else '關係理由' if field == 'r' else '學習重點'}
        targets[key] = (target, field, maximum)

    for index, concept in enumerate(result['concepts']):
        if _needs_translation(concept['l']):
            add(f'label_{index}', concept['l'], concept, 'l', maximum=256)
        for number, claim in enumerate(concept['c']):
            projected = _project_claim(_expand_claim(claim, context['evidence']), evidence)
            if projected is None:
                continue
            sources = [evidence[span['evidence_id']] for span in projected['source_spans']]
            if projected['projection'] == 'source_literal_repair' or _needs_translation(projected['text'], sources):
                original = ' '.join(source['exact_text'] for source in sources)
                literal_source = ' '.join(projected['text'].split()) == ' '.join(original.split())
                add(f'claim_{index}_{number}', projected['text'], claim, 'm', sources=sources if literal_source else None)
    for index, relation in enumerate(result['relations']):
        if _needs_translation(relation['r']):
            add(f'relation_{index}', relation['r'], relation, 'r', maximum=1024)
    keys = list(items)
    for start in range(0, len(keys), 4):
        batch = keys[start:start + 4]
        properties = {key: {'type': 'string', 'minLength': 1, 'maxLength': targets[key][2]} for key in batch}
        schema = {'type': 'object', 'additionalProperties': False, 'required': batch, 'properties': properties}
        translated = call({'localization': {key: items[key] for key in batch}}, schema)
        if not isinstance(translated, dict) or set(translated) != set(batch):
            raise ValueError('SEMANTIC_OUTPUT_INVALID')
        for key in batch:
            value = restore_literals(translated[key], items[key]['literals'])
            if not _chinese(value) or len(' '.join(value.split())) > targets[key][2]:
                raise ValueError('SEMANTIC_OUTPUT_INVALID')
            target, field, _ = targets[key]
            if field == 'l' and target['l'] not in target['a']:
                target['a'].append(target['l'])
            target[field] = value
    # 不讓修正失敗又默默變成整段外文；只能保存完整通過的批次。
    for concept in result['concepts']:
        for claim in concept['c']:
            projected = _project_claim(_expand_claim(claim, context['evidence']), evidence)
            if projected is not None and (projected['projection'] == 'source_literal_repair' or _needs_translation(
                projected['text'], [evidence[span['evidence_id']] for span in projected['source_spans']],
            )):
                raise ValueError('SEMANTIC_OUTPUT_INVALID')
    return result

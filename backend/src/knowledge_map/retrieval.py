"""教材原文的檢索投影；索引只定位內容，不能取代 Evidence 作為依據。"""

from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
import unicodedata

from pdf_evidence.ocr_page_evidence import canonical_sha256


POLICY = 'evidence-passages/v1'
CHUNK_CHARACTERS = 1800
_STOP = set('the and for with from this that what how why are was were can does did have has into about please explain'.split())
_STOP.update(('什麼', '甚麼', '如何', '為何', '為什', '麼是', '可以', '是否', '請問', '說明', '解釋', '教材', '這個', '那個', '它的', '的是', '以及'))


def terms(text):
    """保留技術術語；中文雙字索引避免依賴另一個常駐模型或英文斷詞器。"""
    text = unicodedata.normalize('NFKC', text).casefold()
    result = []
    for part in re.findall(r'[a-z0-9]+(?:[_.+#/-][a-z0-9]+)*|[\u3400-\u9fff]+', text):
        if part[0].isascii():
            if part not in _STOP:
                result.append(part)
        else:
            result.extend(word for word in (part[i:i + 2] for i in range(len(part) - 1)) if word not in _STOP)
    return result


def text_ranges(text, size=CHUNK_CHARACTERS):
    """分段不改原文；每片保存字元範圍，過長段落仍可接續取回相鄰片段。"""
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            breaks = [text.rfind(mark, start + size // 2, end) for mark in ('\n', '。', '. ', '；', '; ')]
            boundary = max(breaks)
            if boundary >= 0:
                end = boundary + 1
        if text[start:end].strip():
            yield start, end
        start = end


def build_passages(document):
    binding = document['input_binding']
    pages = binding['bundle']['pages']
    names = {item['source_id']: item['original_name'] for item in binding['manifest']['items']}
    labels, concepts = defaultdict(list), defaultdict(list)
    for concept in document['concepts']:
        for claim in concept['claims']:
            for identity in claim['evidence_refs']:
                labels[identity].extend([concept['label'], *concept['aliases']])
                concepts[identity].append(concept['concept_id'])
    rows = []
    for evidence in document['evidence']:
        identity = evidence['evidence_id']
        location = pages[evidence['page'] - 1]
        heading = evidence['heading']
        hints = list(dict.fromkeys([heading, *labels[identity]]))
        for start, end in text_ranges(evidence['exact_text']):
            quote = evidence['exact_text'][start:end]
            reference = {key: deepcopy(evidence[key]) for key in (
                'evidence_id', 'page_ref', 'page', 'block_order', 'kind', 'source', 'source_locator')}
            reference.update(quote=quote, source_id=location['source_id'],
                             source_name=names[location['source_id']], normalized_page=location['normalized_page'])
            passage_id = 'passage:sha256:' + canonical_sha256({'revision': document['revision'],
                'evidence_id': identity, 'start': start, 'end': end})
            rows.append({'position': len(rows), 'passage_id': passage_id,
                'evidence_id': identity, 'source_id': location['source_id'],
                'page': evidence['page'], 'section_id': evidence['section_id'],
                'concept_ids': list(dict.fromkeys(concepts[identity])),
                'hints': hints, 'start': start, 'end': end, 'text': quote,
                'evidence': reference})
    if not rows:
        raise ValueError('RETRIEVAL_INDEX_EMPTY')
    return rows


def rank_texts(query, documents, *, limit=24):
    """研究正文的本機候選排序；與教材索引共用斷詞，不額外呼叫模型篩每一段。"""
    query_terms = Counter(terms(query))
    words = [Counter(terms(text)) for text in documents]
    frequency = Counter(term for item in words for term in item)
    average = sum(sum(item.values()) for item in words) / max(1, len(words))
    scores = []
    for index, item in enumerate(words):
        length = sum(item.values())
        score = 0.0
        for term in query_terms:
            count = item[term]
            if count:
                inverse = math.log(1 + (len(words) - frequency[term] + .5) / (frequency[term] + .5))
                score += inverse * count * 2.2 / (count + 1.2 * (.25 + .75 * length / max(1, average)))
        if score > 0:
            scores.append((index, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]


def distinct_passages(passages):
    """相同原文只送一份，來源清單保留供回查；相近或否定不同的內容不自動合併。"""
    found = {}
    result = []
    for passage in passages:
        key = ' '.join(passage['text'].split())
        reference = {**deepcopy(passage['evidence']), 'quote_start': passage['start'], 'quote_end': passage['end']}
        if key in found:
            row = found[key]
            if reference not in row['references']:
                row['references'].append(reference)
            continue
        row = {**deepcopy(passage), 'references': [reference]}
        found[key] = row
        result.append(row)
    return result

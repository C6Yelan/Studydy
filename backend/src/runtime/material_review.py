"""教材發布前的完整地圖整理；來源、提案與套用對照留在私人 analysis archive。"""
from copy import deepcopy
from collections import Counter
import time

from knowledge_map.material_review import (ReviewError, _pack_review, apply_review, combine_reviews,
                                          response_schema, validate_proposal)
from knowledge_map.structure import build_knowledge_structure_view, _revision
from pdf_evidence.ocr_page_evidence import canonical_sha256
from .command_semantics import retain_call_outputs
from .semantic_service import request_semantics, semantic_client


def review_inputs(document):
    """按連續頁段安排完整來源脈絡；跨頁概念只交給首次出現的頁段處理。"""
    view = build_knowledge_structure_view(document)
    binding = document.get('input_binding')
    sources = {s['source_id']: s for s in binding['manifest']['items']} if binding else {}
    evidence = {}
    for source in document['evidence']:
        e = {k: deepcopy(v) for k, v in source.items() if k != 'exact_text'}
        e['quote'] = source['exact_text']
        location = binding['bundle']['pages'][e['page'] - 1] if binding else {
            'source_id': document['material_id'], 'normalized_page': e['page']}
        e.update(source_id=location['source_id'], normalized_page=location['normalized_page'],
                 source_name=sources[location['source_id']]['original_name'] if binding else '原始 PDF')
        evidence[e['evidence_id']] = e
    for c in view['concepts']:
        for q in c['claims']:
            q['evidence'] = [deepcopy(evidence[e['evidence_id']]) for e in q['evidence']]
    owners = {}
    for c in view['concepts']:
        anchor = min((e for q in c['claims'] for e in q['evidence']), key=lambda e: (e['page'], e['block_order']))
        owners.setdefault((anchor['source_id'], anchor['page']), []).append(c)
    chunks, current, source_id, pages = [], [], None, []
    for (source, page), concepts in owners.items():
        # 只在頁面邊界切分，不拆開同頁表格／流程；40 是分批目標，不是刪點配額。
        if current and (source != source_id or len(current) + len(concepts) > 40):
            chunks.append((source_id, pages, current))
            current, pages = [], []
        source_id = source
        current.extend(concepts)
        pages.append(page)
    if current:
        chunks.append((source_id, pages, current))
    units = []
    for source, pages, concepts in chunks:
        refs = {e['evidence_id'] for c in concepts for q in c['claims'] for e in q['evidence']}
        context = [e for e in evidence.values() if e['evidence_id'] in refs or
                   (e['source_id'] == source and min(pages) <= e['page'] <= max(pages))]
        title = f"{context[0]['source_name']}：第 {min(e['normalized_page'] for e in context if e['source_id'] == source)}–{max(e['normalized_page'] for e in context if e['source_id'] == source)} 頁"
        units.append(_pack_review(view, concepts, context, title))
    return view, units


def _checked_review(unit, index, lock, archive, client, check_cancel):
    """概念覆蓋或來源歸屬錯誤最多補正一次；不套用無法驗證的提案。"""
    key = canonical_sha256({'source': unit.source_digest, 'request': unit.payload,
                            'policy': lock['material_review']})
    rejected = None
    rejection_code = None
    repairable = {'REVIEW_CONCEPT_COVERAGE_INVALID', 'REVIEW_CONCEPT_SUPPORT_INVALID'}

    def validate_saved(value):
        nonlocal rejected, rejection_code
        try:
            validate_proposal(unit, value)
        except ReviewError as error:
            if str(error) in repairable and rejected is None:
                rejected = deepcopy(value)
                rejection_code = str(error)
            raise

    response = archive.load_review(key, validate_response=validate_saved)
    if response is not None:
        validate_proposal(unit, response)
        return response, 0

    schema = response_schema()
    schema['properties']['assignments'].update(minItems=len(unit.concepts), maxItems=len(unit.concepts))
    schema['$defs']['Assignment']['properties']['concept']['enum'] = list(range(len(unit.concepts)))
    calls = 0

    def request_review(request, attempt):
        nonlocal calls
        check_cancel()
        output = archive.prepare_review_call(index, key, request, attempt=attempt)
        calls += 1
        with retain_call_outputs(output):
            value = request_semantics(client, runtime_lock=lock, task='material_review',
                                      request=request, response_schema=schema)
        name = f'call-{index:06d}' + (f'-repair-{attempt:02d}' if attempt else '')
        archive.save_review(f'{name}/response', value)
        return value

    if rejected is None:
        response = request_review(unit.payload, 0)
        try:
            validate_saved(response)
        except ReviewError as error:
            if str(error) not in repairable:
                raise
    if rejected is not None:
        counts = Counter(row['concept'] for row in rejected['assignments'])
        expected = set(range(len(unit.concepts)))
        request = deepcopy(unit.payload)
        request['review_correction'] = {
            'error': rejection_code,
            'missing_concepts': sorted(expected - counts.keys()),
            'duplicate_concepts': sorted(h for h, count in counts.items() if count > 1),
            'unexpected_concepts': sorted(counts.keys() - expected),
            'unsupported_concepts': sorted({a['concept'] for a in rejected['assignments']
                if a['concept'] in expected and not set(a['evidence']) &
                set(unit.payload['concepts'][a['concept']]['evidence'])}),
            'concept_source_bindings': [{'concept': c['h'], 'evidence': c['evidence']} for c in unit.payload['concepts']],
            'previous_response': rejected,
            'instruction': '前次提案未通過檢核，不是標準答案。請依提供的教材重新確認衝突或缺漏，回傳完整提案；'
                           'concept 必須沿用輸入 h，絕不可依回應位置重新編號。每個 h 恰好一筆 assignment；'
                           '每筆 evidence 必須至少引用該 h 的 concept_source_bindings 中一個來源。'
                           '不得直接取重複資料的第一筆或最後一筆，也不得只替換 evidence 掩蓋錯誤歸屬。'
                           '無法由來源確認時，使用 needs_review 保留該概念；仍須遵守來源與歸屬限制。',
        }
        response = request_review(request, 1)
        validate_proposal(unit, response)
    archive.save_review(f'cache-{key}', response)
    return response, calls


def review_structure(document, lock, archive, check_cancel, progress):
    """重用已保存的有效批次；coverage 失敗只做有界補正，不重跑頁面分析。"""
    if 'material_review' not in lock:
        return document
    view, units = review_inputs(document)
    archive.save_review('source', document)
    started = time.monotonic()
    reviews, calls = [], 0
    with semantic_client() as client:
        for index, unit in enumerate(units, 1):
            check_cancel()
            response, new_calls = _checked_review(unit, index, lock, archive, client, check_cancel)
            calls += new_calls
            reviews.append((unit, response))
            progress('semantics', document['page_count'], document['page_count'])
    unit, proposal = combine_reviews(view, reviews)
    result, projection = apply_review(document, view, unit, proposal)
    # 與既有分析 checkpoint 相同：計入使用的語意回應；新呼叫另由 receipt 計數。
    result['metrics']['semantic_calls'] += len(units)
    result['metrics']['semantic_duration_ms'] += round((time.monotonic() - started) * 1000)
    result['revision'] = _revision(result)
    archive.save_review('result', {'source_revision': document['revision'], 'applied_revision': result['revision'],
        'model_calls': calls, 'units': len(units), 'reused_batches': deepcopy(archive.review_reuses),
        'projection': projection, 'proposal': proposal})
    return result

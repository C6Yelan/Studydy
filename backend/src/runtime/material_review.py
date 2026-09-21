"""教材發布前的完整地圖整理；來源、提案與套用對照留在私人 analysis archive。"""
from copy import deepcopy
import time

from knowledge_map.material_review import (_pack_review, apply_review, combine_reviews,
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


def review_structure(document, lock, archive, check_cancel, progress):
    """每批只呼叫一次；重試工作只重用相同輸入、設定且已保存的成功回應。"""
    if 'material_review' not in lock:
        return document
    view, units = review_inputs(document)
    archive.save_review('source', document)
    started = time.monotonic()
    reviews, calls = [], 0
    with semantic_client() as client:
        for index, unit in enumerate(units, 1):
            check_cancel()
            request = unit.payload
            cache_key = canonical_sha256({'source': unit.source_digest, 'request': request,
                                          'policy': lock['material_review']})
            response = archive.load_review(cache_key, validate_response=lambda value: validate_proposal(unit, value))
            if response is None:
                output = archive.prepare_review_call(index, cache_key, request)
                calls += 1
                with retain_call_outputs(output):
                    response = request_semantics(client, runtime_lock=lock, task='material_review',
                                                 request=request, response_schema=response_schema())
                archive.save_review(f'call-{index:06d}/response', response)
                validate_proposal(unit, response)
                archive.save_review(f'cache-{cache_key}', response)
            else:
                validate_proposal(unit, response)
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

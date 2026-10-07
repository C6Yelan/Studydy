"""單一未使用 OCR 區塊超額時，從原 PDF 區域重辨識；不改已引用的 Evidence。"""
from copy import deepcopy
import base64
import hashlib
from pathlib import Path
import tempfile

import pymupdf

from .local_ai_process import LocalAIError, start_ocr_process
from .ocr_page_evidence import canonical_sha256, _normalized_text
from .source_pdf import snapshot_pdf_source


def recover_ocr_block(evidence, state, source_inputs, binding, settings, check_cancel):
    old_id = evidence['evidence_id']
    used = {span['evidence_id'] for concept in state.concepts.values()
            for claim in concept['claims'] for span in claim['source_spans']}
    used.update(ref for relation in state.relations for ref in relation['evidence_refs'])
    if evidence['source'] != 'unlimited_ocr' or evidence['kind'] == 'heading' or old_id in used:
        raise ValueError('SEMANTIC_INPUT_TOO_LARGE')
    source_page = binding['bundle']['pages'][evidence['page'] - 1]
    index = next(i for i, item in enumerate(binding['manifest']['items'])
                 if item['source_id'] == source_page['source_id'])
    check_cancel()
    with tempfile.TemporaryDirectory(prefix='studydy-ocr-recovery-') as temporary:
        path = Path(temporary) / 'source.pdf'
        snapshot_pdf_source(source_inputs[index], path)
        with pymupdf.open(path) as document:
            page = document[source_page['normalized_page'] - 1]
            # Evidence 座標為未旋轉 PDF points；裁切前轉回可見頁面座標。
            region = pymupdf.Rect(evidence['source_locator']['region']) * page.rotation_matrix
            pixmap = page.get_pixmap(dpi=200, colorspace=pymupdf.csRGB, alpha=False, clip=region)
            png = pixmap.tobytes('png')
            render = {'sha256': hashlib.sha256(png).hexdigest(), 'width': pixmap.width,
                      'height': pixmap.height, 'png_base64': base64.b64encode(png).decode('ascii')}
    check_cancel()
    ocr = start_ocr_process(settings)
    try:
        response = ocr.request({'schema': 'local-ocr-request/v1', 'request_id': 'recover-block',
                                'render': render}, None, cancellation_check=check_cancel)
    except BaseException:
        ocr.abort()
        raise
    else:
        ocr.close()
    if (not isinstance(response, dict)
        or set(response) != {'schema', 'request_id', 'blocks'}
        or response['schema'] != 'local-ocr-response/v1'
        or response['request_id'] != 'recover-block'
        or not isinstance(response['blocks'], list) or not response['blocks']):
        raise LocalAIError('CHILD_RESPONSE_INVALID')
    try:
        text = '\n'.join(_normalized_text(block['text']) for block in response['blocks'])
    except (KeyError, TypeError, ValueError):
        raise LocalAIError('OCR_OUTPUT_INVALID') from None
    replacement = deepcopy(evidence)
    replacement['exact_text'] = text
    replacement['evidence_id'] = 'evidence:sha256:' + canonical_sha256({
        'page_ref': evidence['page_ref'], 'block_id': evidence['source_locator']['block_id'],
        'kind': evidence['kind'], 'source': evidence['source'], 'text': text,
        'reading_order': evidence['block_order'], 'region': evidence['source_locator']['region'],
    })
    # 新文字仍定位在同一原始 PDF 區域；舊 ID 與舊 checkpoint 不覆寫。
    return replacement, {
        'original_evidence_id': old_id, 'replacement': replacement,
        'source_id': source_page['source_id'], 'normalized_page': source_page['normalized_page'],
        'render_sha256': render['sha256'], 'render_width': render['width'], 'render_height': render['height'],
        'generation': {'no_repeat_ngram_size': 35, 'ngram_window': 256, 'temperature': 0.0},
    }


def replace_unprocessed_evidence(context, index, replacement):
    old_id = context['evidence'][index]['evidence_id']
    context['evidence'][index] = replacement
    for section in context['sections']:
        section['evidence_ids'] = [replacement['evidence_id'] if ref == old_id else ref
                                   for ref in section['evidence_ids']]

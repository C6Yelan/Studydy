"""教材發布前的完整地圖整理；來源、提案與套用對照留在私人 analysis archive。"""
from copy import deepcopy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Event
import os
import time

from knowledge_map.material_review import (ReviewError, _pack_review, apply_review, combine_reviews,
                                          response_schema, validate_proposal)
from knowledge_map.localization import POLICY as LOCALIZATION_POLICY, _needs_translation
from knowledge_map.claim_literals import language_correction_template, restore_literals
from knowledge_map.structure import build_knowledge_structure_view, _revision
from knowledge_map.structure_rules import RELATION_TYPES
from content_identity import canonical_sha256
from .semantic_service import SemanticServiceError, request_semantics, semantic_client


def review_inputs(document, *, preserved_concept_ids=frozenset()):
    """按連續頁段安排完整來源脈絡；跨頁概念只交給首次出現的頁段處理。"""
    view = build_knowledge_structure_view(document)
    binding = document['input_binding']
    sources = {s['source_id']: s for s in binding['manifest']['items']}
    evidence = {}
    for source in document['evidence']:
        e = {k: deepcopy(v) for k, v in source.items() if k != 'exact_text'}
        e['quote'] = source['exact_text']
        location = binding['bundle']['pages'][e['page'] - 1]
        e.update(source_id=location['source_id'], normalized_page=location['normalized_page'],
                 source_name=sources[location['source_id']]['original_name'])
        evidence[e['evidence_id']] = e
    for c in view['concepts']:
        for q in c['claims']:
            q['evidence'] = [deepcopy(evidence[e['evidence_id']]) for e in q['evidence']]
    owners = {}
    for c in view['concepts']:
        if c['concept_id'] in preserved_concept_ids:
            continue
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
        cited_pages = {e['page'] for c in concepts for q in c['claims'] for e in q['evidence']}
        context = [e for e in evidence.values() if e['page'] in cited_pages or
                   (e['source_id'] == source and min(pages) <= e['page'] <= max(pages))]
        title = f"{context[0]['source_name']}：第 {min(e['normalized_page'] for e in context if e['source_id'] == source)}–{max(e['normalized_page'] for e in context if e['source_id'] == source)} 頁"
        unit=_pack_review(view, concepts, context, title)
        scope=binding['bundle'].get('learning_scopes',{}).get(source)
        if scope:
            unit.payload['learning_scope']=deepcopy(scope)
            unit.payload['learning_scope_policy']='依已確認的目標、主題細節及其中明列的限制整理。相鄰原文只供理解，不把範圍外細節變成必學概念；必要的前提、反例與限制仍須保留。'
        units.append(unit)
    return view, units


def _checked_review(unit, index, lock, archive, client, check_cancel, wait_cancellation_check=None):
    """可定位的提案契約錯誤最多補正一次；不套用無法驗證的提案。"""
    localized = lock.get('material_semantics', {}).get('localization_policy') == LOCALIZATION_POLICY
    translations = [i for i, claim in enumerate(unit.claims) if localized and _needs_translation(claim['text'], claim['evidence'])]
    payload = deepcopy(unit.payload)
    if translations:
        handles = {e['evidence_id']: i for i, e in enumerate(unit.evidence)}
        payload['language_corrections'] = []
        for i in translations:
            template, literals = language_correction_template(unit.claims[i]['text'])
            payload['language_corrections'].append({'claim': i, 'original_evidence': [
                handles[e['evidence_id']] for e in unit.claims[i]['evidence']], 'text': template, 'literals': literals,
                'instruction': 'claim_edits.meaning 必須沿用 text 的所有保留代號且每個恰好一次，不可展開代號。僅修正未保護的外語文字，literals 只供理解原上下文。'})
    # 完整地圖 revision 包含本次 run 身分；重試仍須能重用相同原文與請求的審查。
    # source_digest 繼續用於套用前的整圖核對，快取則綁定本批實際來源。
    key = canonical_sha256({'source': canonical_sha256(unit.evidence), 'request': payload,
                            'policy': lock['material_review']})
    rejected = None
    rejection_code = None
    repairable = {'REVIEW_CONCEPT_COVERAGE_INVALID', 'REVIEW_CONCEPT_SUPPORT_INVALID',
                  'REVIEW_RELATION_EDIT_INVALID', 'REVIEW_EDIT_ID_INVALID',
                  'REVIEW_ALIAS_EDIT_INVALID', 'REVIEW_METADATA_REASON_INVALID',
                  'REVIEW_TARGET_INVALID', 'REVIEW_GROUPING_CYCLE',
                  'REVIEW_EVIDENCE_INVALID', 'REVIEW_CLAIM_EDIT_INVALID',
                  'REVIEW_RESPONSE_INVALID'}

    def validate_saved(value):
        nonlocal rejected, rejection_code
        try:
            validate_proposal(unit, value)
        except ReviewError as error:
            if str(error) in repairable and rejection_code is None:
                rejected = deepcopy(value)
                rejection_code = str(error)
            raise

    response = archive.load_review(key, validate_response=validate_saved)
    if response is not None:
        validate_saved(response)
        return response, 0

    schema = response_schema()
    schema['$defs']['ClaimEdit']['properties']['meaning']['description'] = (
        '若此 claim 列在 language_corrections，沿用其 text 範本與保留代號，僅翻譯未保護的文字；不可展開代號。'
    )
    schema['$defs']['AliasEdit']['properties']['remove']['description'] = (
        'Copy exact strings from this concept\'s input aliases, including case. '
        'The concept label is not an alias unless it also appears in aliases. '
        'Do not repeat strings; omit the edit when no existing alias should be removed.'
    )
    handles = [str(index) for index in range(len(unit.concepts))]
    schema['properties']['assignments'] = {
        'type': 'object', 'additionalProperties': False, 'required': handles,
        'properties': {str(concept['h']): {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'concept_source': {'type': 'integer', 'enum': concept['evidence'],
                    'description': 'Select this concept\'s source supporting the review decision.'},
                'decision': {'$ref': '#/$defs/Assignment'},
            },
            'required': ['concept_source', 'decision'],
        } for concept in unit.payload['concepts']},
        'description': 'Each required key is the exact input concept h. Review that concept in its own field.',
    }
    # 動作與理由／歸屬一起約束，避免 schema 接受 metadata + none 等矛盾組合。
    assignment = schema['$defs']['Assignment']
    del assignment['properties']['concept']
    assignment['required'].remove('concept')
    assignment['properties']['evidence']['minItems'] = 0
    variants = []
    for actions in (['keep', 'needs_review'], ['group', 'example'], ['metadata']):
        variant = deepcopy(assignment)
        properties = variant['properties']
        properties['action'] = {'type': 'string', 'enum': actions}
        properties['target'] = ({'type': 'integer', 'enum': list(range(len(unit.concepts)))}
                                if 'group' in actions else {'type': 'null'})
        if actions == ['metadata']:
            properties['issue'] = {'type': 'string', 'enum': ['author', 'layout', 'scale']}
        variants.append(variant)
    schema['$defs']['Assignment'] = {'anyOf': variants}
    for collection, definition, field, count in (
        ('alias_edits', 'AliasEdit', 'concept', len(unit.concepts)),
        ('claim_edits', 'ClaimEdit', 'claim', len(unit.claims)),
        ('relation_edits', 'RelationEdit', 'relation', len(unit.relations)),
    ):
        schema['properties'][collection]['description'] = (
            f'Each {field} handle may appear at most once in this array. '
            'Do not submit multiple operations for the same handle; retain it unchanged if uncertain.'
        )
        if count:
            schema['$defs'][definition]['properties'][field]['enum'] = list(range(count))
        else:
            schema['properties'][collection]['maxItems'] = 0
    # 別名是輸入中的完整字串；schema 直接綁定概念與可移除項，避免把複合別名拆詞。
    alias_choices = []
    for concept in unit.payload['concepts']:
        if not concept['aliases']:
            continue
        choice = deepcopy(schema['$defs']['AliasEdit'])
        choice['properties']['concept'] = {'type': 'integer', 'enum': [concept['h']]}
        choice['properties']['remove']['items'] = {'type': 'string', 'enum': list(dict.fromkeys(concept['aliases']))}
        choice['properties']['concept_source'] = {
            'type': 'integer', 'enum': concept['evidence'],
            'description': 'Select a source belonging to this concept that supports the alias edit.',
        }
        choice['required'].append('concept_source')
        choice['properties']['evidence']['minItems'] = 0
        alias_choices.append(choice)
    if alias_choices:
        schema['properties']['alias_edits']['items'] = {'anyOf': alias_choices}
        del schema['$defs']['AliasEdit']
    else:
        schema['properties']['alias_edits']['maxItems'] = 0
    relation_choices = []
    support_sets = {}
    definitions = schema['$defs']
    if unit.payload['relations']:
        # 共用 handle／來源集合，避免各分支重複 enum 而超過 provider 的 schema 限制。
        definitions['RelationKind'] = {'type': 'string', 'enum': sorted(RELATION_TYPES)}
        definitions['FixedAction'] = {'type': 'string', 'enum': ['reverse', 'remove']}
        definitions['RetypeAction'] = {'type': 'string', 'enum': ['retype']}
    for relation in unit.payload['relations']:
        refs = tuple(sorted(set(unit.payload['concepts'][relation['source']]['evidence'] +
                                unit.payload['concepts'][relation['target']]['evidence'])))
        support_name = support_sets.setdefault(refs, f'S{len(support_sets)}')
        for ref in refs:
            definitions[f'E{ref}'] = {'type': 'integer', 'enum': [ref]}
        definitions[support_name] = {'type': 'array', 'minItems': 1,
            'items': {'anyOf': [{'$ref': f'#/$defs/E{ref}'} for ref in refs]}}
        relation_name = f"R{relation['h']}"
        definitions[relation_name] = {'type': 'integer', 'enum': [relation['h']]}
        original_kind = f"Original_{relation['type']}"
        definitions[original_kind] = {'type': ['string', 'null'], 'enum': [None, relation['type']]}
        for action, kind in (('FixedAction', original_kind), ('RetypeAction', 'RelationKind')):
            choice = deepcopy(schema['$defs']['RelationEdit'])
            properties = choice['properties']
            properties['relation'] = {'$ref': f'#/$defs/{relation_name}'}
            properties['action'] = {'$ref': f'#/$defs/{action}'}
            properties['relation_type'] = {'$ref': f'#/$defs/{kind}'}
            # 新提案以關係兩端的原引用作支持；後端仍獨立核對，不代改模型的引用。
            properties['evidence'] = {'$ref': f'#/$defs/{support_name}'}
            # 先選關係與操作，再填該關係允許的引用。
            choice['properties'] = {key: properties[key] for key in
                                    ('relation', 'action', 'relation_type', 'evidence', 'reason')}
            relation_choices.append(choice)
    if relation_choices:
        schema['properties']['relation_edits']['items'] = {'anyOf': relation_choices}
        del definitions['RelationEdit']
    calls = 0

    def request_review(request, attempt):
        nonlocal calls, rejected, rejection_code
        check_cancel()
        request = {**request, 'assignment_format':
            'assignments 是以輸入 concept h 的十進位字串為 key 的物件。每個指定 key 都必須填寫；'
            '其內容只審查該 key 對應的概念，不另填 concept 欄位。target 仍使用輸入的整數 h。'
            '每個概念先用 concept_source 選取自己的實際支持來源，再把審查結果填入 decision；'
            'decision.evidence 可另列補充來源。來源不足以支持改動時保留概念或標記 needs_review。'
            '每筆 alias_edits 的 concept_source 必須由該概念的來源中選擇；evidence 可另列補充來源。'
            '請核對來源是否確實支持移除該別名，支持不足就省略修改，不為填滿欄位而修改。'}
        archive.prepare_review_call(index, key, request, attempt=attempt)
        calls += 1
        try:
            value = request_semantics(client, runtime_lock=lock, task='material_review',
                                      request=request, response_schema=schema,
                                      cancellation_check=wait_cancellation_check or check_cancel)
        except SemanticServiceError as error:
            if error.reason_code == 'SEMANTIC_INPUT_TOO_LARGE':
                # 容量拒絕發生在 inference 前；先前可能已有一份待補正回應，仍須計入。
                error.request_metadata = {**(error.request_metadata or {}), 'review_model_calls': calls - 1}
            raise
        name = f'call-{index:06d}' + (f'-repair-{attempt:02d}' if attempt else '')
        archive.save_review(f'{name}/wire-response', value)
        # 固定欄位承載概念身分，不靠陣列順序或替模型修正重複編號。
        assignments = value.get('assignments') if isinstance(value, dict) else None
        aliases = value.get('alias_edits') if isinstance(value, dict) else None
        if not isinstance(assignments, dict) or any(
            not isinstance(handle, str) or not handle.isascii() or not handle.isdecimal()
            or str(int(handle)) != handle or not isinstance(row, dict) or 'concept' in row
            or 'concept_source' not in row or not isinstance(row.get('decision'), dict)
            or 'concept' in row['decision'] or not isinstance(row['decision'].get('evidence'), list)
            for handle, row in assignments.items()
        ) or not isinstance(aliases, list) or any(
            not isinstance(edit, dict) or 'concept_source' not in edit
            or not isinstance(edit.get('evidence'), list) for edit in aliases
        ):
            if rejection_code is None:
                rejected, rejection_code = deepcopy(value), 'REVIEW_RESPONSE_INVALID'
            raise ReviewError('REVIEW_RESPONSE_INVALID')
        value = {**value,
            'assignments': [{'concept': int(handle), **row['decision'],
                             'evidence': [row['concept_source'], *row['decision']['evidence']]}
                            for handle, row in assignments.items()],
            # 兩種引用都由模型明確選取；不替它補上任意一筆既有來源。
            'alias_edits': [{**{key: item for key, item in edit.items() if key != 'concept_source'},
                            'evidence': [edit['concept_source'], *edit['evidence']]}
                           for edit in aliases],
        }
        edits = value.get('claim_edits')
        for edit in edits if isinstance(edits, list) else []:
            if not isinstance(edit, dict) or type(edit.get('claim')) is not int:
                continue  # 完整提案驗證會回報格式錯誤，不在文字還原時猜測編號。
            correction = next((item for item in payload.get('language_corrections', []) if item['claim'] == edit['claim']), None)
            if correction is not None:
                try:
                    edit['meaning'] = restore_literals(edit['meaning'], correction['literals'])
                except (ValueError, KeyError):
                    if rejection_code is None:
                        rejected, rejection_code = deepcopy(value), 'REVIEW_CLAIM_EDIT_INVALID'
                    raise ReviewError('REVIEW_CLAIM_EDIT_INVALID') from None
        archive.save_review(f'{name}/response', value)
        return value

    if rejection_code is None:
        try:
            response = request_review(payload, 0)
            validate_saved(response)
        except ReviewError as error:
            if str(error) not in repairable:
                raise
    if rejection_code is not None:
        # 格式本身錯誤時不索引不可信欄位；仍把原回應交回模型，要求完整補正。
        parsed = rejected if rejection_code != 'REVIEW_RESPONSE_INVALID' else {
            key: [] for key in ('assignments', 'alias_edits', 'claim_edits', 'relation_edits')}
        counts = Counter(row['concept'] for row in parsed['assignments'])
        expected = set(range(len(unit.concepts)))
        request = deepcopy(payload)
        edit_id_issues = {}
        for collection, field, limit in (
            ('alias_edits', 'concept', len(unit.concepts)),
            ('claim_edits', 'claim', len(unit.claims)),
            ('relation_edits', 'relation', len(unit.relations)),
        ):
            handles = Counter(row[field] for row in parsed[collection])
            edit_id_issues[collection] = {
                'duplicates': sorted(h for h, count in handles.items() if count > 1),
                'unexpected': sorted(h for h in handles if h not in range(limit)),
            }
        request['review_correction'] = {
            'error': rejection_code,
            'edit_id_issues': edit_id_issues,
            'invalid_metadata_reasons': [
                {'concept': a['concept'], 'issue': a['issue']}
                for a in parsed['assignments']
                if a['action'] == 'metadata' and a['issue'] not in {'author', 'layout', 'scale'}
            ],
            # 只指出輸入與提案的差異；不以大小寫折疊替模型選擇要刪除的別名。
            'alias_edit_issues': [
                {'concept': edit['concept'], 'allowed_aliases': concept['aliases'],
                 'unknown_removals': [alias for alias in edit['remove'] if alias not in concept['aliases']],
                 'duplicate_removals': [alias for alias, count in Counter(edit['remove']).items() if count > 1],
                 'has_concept_support': bool(set(edit['evidence']) & set(concept['evidence']))}
                for edit in parsed['alias_edits']
                for concept in unit.payload['concepts'] if concept['h'] == edit['concept']
            ],
            'missing_concepts': sorted(expected - counts.keys()),
            'duplicate_concepts': sorted(h for h, count in counts.items() if count > 1),
            'unexpected_concepts': sorted(counts.keys() - expected),
            'unsupported_concepts': sorted({a['concept'] for a in parsed['assignments']
                if a['concept'] in expected and not set(a['evidence']) &
                set(unit.payload['concepts'][a['concept']]['evidence'])}),
            'concept_source_bindings': [{'concept': c['h'], 'evidence': c['evidence']} for c in unit.payload['concepts']],
            'relation_source_bindings': [
                {'relation': r['h'], 'type': r['type'], 'evidence': sorted(set(
                    unit.payload['concepts'][r['source']]['evidence'] + unit.payload['concepts'][r['target']]['evidence']))}
                for r in unit.payload['relations']
            ],
            'previous_response': rejected,
            'instruction': '前次提案未通過檢核，不是標準答案。請依提供的教材重新確認衝突或缺漏，回傳完整提案；'
                           'concept 必須沿用輸入 h，絕不可依回應位置重新編號。每個 h 恰好一筆 assignment；'
                           'metadata 只限明確作者、版面資訊或表格格值，issue 分別為 author、layout、scale。'
                           '必須重新確認內容確屬這些範圍，不可只換成合法理由代碼；不確定時保留 needs_review。'
                           'keep、needs_review、metadata 的 target 必須為 null；group/example 指向本批另一個非 metadata 概念，歸屬不可成環。'
                           'evidence 必須是本批提供的編號，reason 與修正後的 meaning 不可空白。'
                           '每筆 evidence 必須至少引用該 h 的 concept_source_bindings 中一個來源。'
                           '不得直接取重複資料的第一筆或最後一筆，也不得只替換 evidence 掩蓋錯誤歸屬。'
                           'alias_edits.remove 必須逐字引用該 concept 的既有 aliases，大小寫也必須相同且不可重複；'
                           'label 不等於 aliases，不能把概念名稱當成可移除的別名。'
                           '請重新判斷是否確有錯誤別名；沒有來源支持移除時省略該 edit，不猜測或自行正規化。'
                           'relation_edits 的 evidence 只能引用對應關係兩端的來源；reverse/remove 的 relation_type 須為 null 或原型別。'
                           '反轉與改型別不可合成同一操作；若無充分依據可不修改這條關係，不猜測。'
                           '各 edit 陣列的同一 h 最多出現一次，反轉與改型別也不能拆成兩筆相同 h。'
                           '請重新核對來源，選擇一個有依據的操作；無法決定時保留原關係，不機械保留第一筆或最後一筆。'
                           '無法由來源確認時，使用 needs_review 保留該概念；仍須遵守來源與歸屬限制。',
        }
        response = request_review(request, 1)
        validate_saved(response)
    archive.save_review(f'cache-{key}', response)
    return response, calls


def _review_part(view, unit, concepts, title, *, cited_pages_only=False):
    pages = {}
    for concept in concepts:
        for claim in concept['claims']:
            for evidence in claim['evidence']:
                pages.setdefault(evidence['source_id'], set()).add(evidence['normalized_page'])
    evidence = [e for e in unit.evidence if e['source_id'] in pages and (
        e['normalized_page'] in pages[e['source_id']] if cited_pages_only else
        min(pages[e['source_id']]) <= e['normalized_page'] <= max(pages[e['source_id']])
    )]
    part = _pack_review(view, concepts, evidence, title)
    # 拆批不能丟掉使用者已確認的學習範圍。
    for key in ('learning_scope', 'learning_scope_policy'):
        if key in unit.payload:
            part.payload[key] = deepcopy(unit.payload[key])
    return part


def _review_units(view, units, lock, archive, check_cancel, progress, wait_cancellation_check=None):
    """協調者分派／合併；失敗後不再派送，收完在途批次以保留有效結果。"""
    from .luna_test import enabled
    width = 2 if os.environ.get('STUDYDY_LUNA_PARALLEL_TEST') == '1' and enabled(lock) else 1
    units = list(units)
    reviews, calls, split_units, cursor = {}, 0, 0, 0
    stopped = Event()
    pending = {}
    failure = None

    def check_wait():
        (wait_cancellation_check or check_cancel)()
        if stopped.is_set():
            raise ReviewError('REVIEW_INTERRUPTED')

    def generate(index, unit):
        with semantic_client() as client:
            return _checked_review(unit, index, lock, archive, client, check_wait, check_wait)

    progress("review", 0, len(units))
    pool = ThreadPoolExecutor(max_workers=width, thread_name_prefix='material-review')
    try:
        while pending or (failure is None and cursor < len(units)):
            check_cancel()
            while failure is None and cursor < len(units) and len(pending) < width:
                unit = units[cursor]
                cursor += 1
                pending[pool.submit(generate, cursor, unit)] = (cursor, unit)
            done, _ = wait(pending, timeout=.25, return_when=FIRST_COMPLETED)
            for future in done:
                index, unit = pending.pop(future)
                try:
                    response, new_calls = future.result()
                except SemanticServiceError as error:
                    if error.reason_code != 'SEMANTIC_INPUT_TOO_LARGE':
                        failure = failure or error
                        continue
                    calls += (error.request_metadata or {}).get('review_model_calls', 0)
                    if len(unit.concepts) == 1:
                        # 舊分段先按原輸入重用成功快取。只對仍超額的單一概念收斂來源頁面，
                        # 保留每個引用及其所在頁的完整區塊，不截文字、不刪 Claim 或改 Evidence。
                        part = _review_part(view, unit, unit.concepts,
                            f"{unit.payload['title']}（引用頁面完整來源）", cited_pages_only=True)
                        if len(part.evidence) == len(unit.evidence):
                            failure = failure or error
                            continue
                        units.append(part)
                        split = {'reason': error.reason_code, 'concept_counts': [1],
                                 'source_scope': 'cited-pages/v1',
                                 'before_evidence_blocks': len(unit.evidence),
                                 'after_evidence_blocks': len(part.evidence)}
                    else:
                        middle = len(unit.concepts) // 2
                        # 同一來源的相鄰頁一起拆；每個子批次保留其引用跨度內的完整頁面，
                        # 不再把整個父批次原文重複送到每個子批次。
                        ordered = sorted(unit.concepts, key=lambda c: min(
                            (e['page'], e['block_order']) for q in c['claims'] for e in q['evidence']))
                        parts = [ordered[:middle], ordered[middle:]]
                        units.extend(_review_part(view, unit, concepts,
                            f"{unit.payload['title']}（容量分段 {part}/2）")
                            for part, concepts in enumerate(parts, 1))
                        split = {'reason': error.reason_code, 'concept_counts': [len(part) for part in parts]}
                    archive.save_review(f'call-{index:06d}/split', split)
                    split_units += 1
                except Exception as error:
                    failure = failure or error
                    continue
                else:
                    calls += new_calls
                    reviews[index] = (unit, response)
                progress("review", len(reviews), len(units) - split_units)
        if failure is not None:
            raise failure
    finally:
        stopped.set()
        pool.shutdown(wait=True, cancel_futures=True)
    # 完成順序不決定套用順序；以原地圖的概念順序固定跨批合併。
    positions = {c['concept_id']: i for i, c in enumerate(view['concepts'])}
    ordered = sorted(reviews.values(), key=lambda item: min(positions[c['concept_id']] for c in item[0].concepts))
    return ordered, calls


def review_structure(document, lock, archive, check_cancel, progress, *, wait_cancellation_check=None, base_structure=None):
    """重用已保存的有效批次，檢核與發布仍由單一協調者完成。"""
    from .material_consolidation import consolidate_structure
    if 'material_review' not in lock:
        return document
    from knowledge_map.source_identity import unchanged_concepts
    preserved = unchanged_concepts(base_structure, document) if base_structure is not None else set()
    view, units = review_inputs(document, preserved_concept_ids=preserved)
    archive.save_review('source', document)
    archive.save_review('scope', {'policy': 'changed-content-and-incident-relations/v1',
        'base_revision': base_structure['revision'] if base_structure else None,
        'preserved_concept_ids': sorted(preserved),
        'reviewed_concept_ids': [c['concept_id'] for unit in units for c in unit.concepts]})
    if not units:
        progress('review', 0, 0)
        archive.save_review('result', {'source_revision': document['revision'], 'applied_revision': document['revision'],
            'model_calls': 0, 'units': 0, 'reused_batches': [], 'projection': None, 'proposal': None})
        return consolidate_structure(document, lock, archive, check_cancel, progress,
                                     wait_cancellation_check=wait_cancellation_check)
    started = time.monotonic()
    reviews, calls = _review_units(view, units, lock, archive, check_cancel, progress, wait_cancellation_check)
    unit, proposal = combine_reviews(view, reviews)
    localized = lock.get('material_semantics', {}).get('localization_policy') == LOCALIZATION_POLICY
    # 整理提案不是重新匯入門檻。局部翻譯未改善時，保留已可回查的原主張，
    # 不因一筆混用外文的修正就讓整批補充失敗。
    language_edits = [edit for edit in proposal['claim_edits'] if localized and _needs_translation(
        edit['meaning'], [*unit.claims[edit['claim']]['evidence'], *(unit.evidence[h] for h in edit['evidence'])],
    )]
    proposal['claim_edits'] = [edit for edit in proposal['claim_edits'] if edit not in language_edits]
    result, projection = apply_review(document, view, unit, proposal)
    projection['blocked_changes'].extend({'claim_id': unit.claims[edit['claim']]['claim_id'],
        'reason': 'LOCALIZATION_RETAINED_ORIGINAL'} for edit in language_edits)
    reviewed_claims = {q['claim_id'] for part in units for q in part.claims}
    evidence_by_id = {item['evidence_id']: item for item in result['evidence']}
    language_pending = localized and any(q['claim_id'] in reviewed_claims and _needs_translation(
                    q['text'], [evidence_by_id[ref] for ref in q['evidence_refs']],
                )
                for c in result['concepts'] for q in c['claims'])
    if language_edits or language_pending:
        result['source_review_required'] = True
        if 'SOURCE_REVIEW_SUGGESTED' not in result['status']['reason_codes']:
            result['status']['reason_codes'].append('SOURCE_REVIEW_SUGGESTED')
        result['status'].update(processing='partial', quality='needs_review', decision='review')
    # 語意回應用量含重用；新增呼叫由 receipt 分別計數。
    result['metrics']['semantic_calls'] += len(reviews)
    result['metrics']['semantic_duration_ms'] += round((time.monotonic() - started) * 1000)
    result['revision'] = _revision(result)
    archive.save_review('result', {'source_revision': document['revision'], 'applied_revision': result['revision'],
        'model_calls': calls, 'units': len(reviews), 'reused_batches': deepcopy(archive.review_reuses),
        'projection': projection, 'proposal': proposal})
    # 同一持久 review 階段接續去重，不把已完成批次的進度倒退成零。
    def consolidation_progress(stage, completed, total):
        progress(stage, len(reviews) + completed, len(reviews) + total)

    return consolidate_structure(result, lock, archive, check_cancel, consolidation_progress,
                                 wait_cancellation_check=wait_cancellation_check)

"""口語節錄的引用邊界與下游保留；合成案例不代表真實辨識品質。"""

from copy import deepcopy
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
import json

import pytest

from content_identity import canonical_sha256
from knowledge_map.claim_literals import EXCERPT_PROJECTION, EXCERPT_REPAIR
from knowledge_map.independent_blocks import block_request, block_schema, project_block
from knowledge_map.localization import localize_block
from knowledge_map.semantic_projection import SemanticState, _project_claim
from knowledge_map.structure import build_document_context
from knowledge_map.structure_rules import _revision
from knowledge_map.structure_validation import validate_knowledge_structure
from knowledge_map.material_review import apply_review
from knowledge_map import consolidation
from runtime.material_review import review_inputs, review_structure
from runtime.material_runtime import validate_runtime_lock, runtime_binding, same_material_runtime
from runtime.local_ai_config import _runtime_lock
from structure_fixtures import build_knowledge_structure
from test_knowledge_structure_v1 import _page, _block
from test_material_consolidation import keep
from test_material_review import wire_proposal
from test_review_parallel_feasibility import Archive
from test_material_pipeline_v1 import _settings

SOURCE = '00000000-0000-4000-8000-000000000001'
TRANSCRIPT = {'format': 'audio', 'transcript_source': 'whisper', 'language': 'zh'}


def prepared(text, meanings):
    context = build_document_context([_page(1, [_block(1, 0, 'paragraph', text)])], page_count=1)
    context['source_pages'] = [{'page': 1, 'source_id': SOURCE, 'normalized_page': 1}]
    context['spoken_sources'] = {SOURCE: TRANSCRIPT}
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    response = {'concepts': [{'l': '教學觀念', 'a': [], 'c': [
        {'m': meaning, 's': [{'e': 0, 'q': quote}]} for meaning, quote in meanings
    ]}], 'relations': []}
    return context, bundle, response


def document(text, meanings):
    context, bundle, response = prepared(text, meanings)
    state = project_block(context, bundle, 'synthetic', response)
    doc = build_knowledge_structure(
        context, state, source_sha256='1' * 64,
        run_id=SOURCE, produced_at='2026-10-25T00:00:00+00:00',
        runtime_lock_sha256='2' * 64, model_id='synthetic', model_revision='synthetic',
        semantic_calls=1, ocr_calls=0,
    )
    return doc


def proposal(unit, *, issue='none', meaning=None):
    return {'assignments': [{'concept': i, 'action': 'needs_review' if issue == 'uncertain' else 'keep',
        'target': None, 'issue': issue, 'reason': '逐字稿用詞待核對。' if issue == 'uncertain' else '獨立觀念。',
        'evidence': c['evidence']} for i, c in enumerate(unit.payload['concepts'])],
        'alias_edits': [], 'relation_edits': [],
        'claim_edits': [] if meaning is None else [{'claim': 0, 'meaning': meaning,
            'evidence': [0], 'reason': '來源敘述整理。'}]}


def test_one_evidence_can_be_split_without_copying_other_facts_or_narration():
    text = '大家好，接下來介紹設定。重試上限為 8 次。等待時間為 5 秒。'
    meanings = [('最多重試 8 次。', '重試上限為 8 次。'), ('須等待 5 秒。', '等待時間為 5 秒。')]
    context, bundle, response = prepared(text, meanings)
    request = block_request(context, bundle)
    assert request['spoken_sources'] == {SOURCE: TRANSCRIPT}
    assert request['sections'][0]['evidence'][0][3] == text
    schema = block_schema([0], excerpt_handles={0})
    assert schema['properties']['concepts']['items']['properties']['c']['items']['properties']['s']['items']['properties']['e']['enum'] == [0]
    doc = document(text, meanings)
    claims = doc['concepts'][0]['claims']
    assert [q['text'] for q in claims] == [m for m, _ in meanings]
    assert all(q['projection'] == EXCERPT_PROJECTION for q in claims)
    assert doc['evidence'][0]['exact_text'] == text and validate_knowledge_structure(doc)
    # 舊版投影不能因新版支援節錄而被放寬。
    assert _project_claim({'meaning': meanings[0][0], 'source_spans': claims[0]['source_spans']},
                          {e['evidence_id']: e for e in doc['evidence']}) is None


@pytest.mark.parametrize('text,quote', [
    ('限制為 18 秒。', '8 秒。'),
    ('甲說相同。乙說相同。', '相同。'),
    ('不能取消。', '可以取消。'),
])
def test_excerpt_rejects_cut_literals_ambiguous_and_invented_quotes(text, quote):
    context, bundle, response = prepared(text, [(quote, quote)])
    result = project_block(context, bundle, 'synthetic', response)
    assert result.rejected_claims == 1 and not next(iter(result.concepts.values()))['claims']


def test_wrong_asr_number_is_restored_only_from_the_cited_excerpt():
    text = '今天介紹 713 事件。下個議題有 9 個案例。'
    context, bundle, response = prepared(text, [('今天介紹 731 事件。', '今天介紹 713 事件。')])
    result = project_block(context, bundle, 'synthetic', response)
    claim = next(iter(result.concepts.values()))['claims'][0]
    assert claim['text'] == '今天介紹 713 事件。' and claim['projection'] == EXCERPT_REPAIR
    assert result.literal_repairs == 1


def test_localization_uses_excerpt_and_preserves_disjoint_quotes():
    quote = 'The limit is 8 attempts.'
    context, bundle, response = prepared(quote + ' We will next discuss 5 other topics.', [(quote, quote)])
    requests = []
    def translate(request, schema):
        requests.append(request)
        item = request['localization']['claim_0_0']
        token = next(iter(item['literals']))
        assert '5' not in item['text'] and 'next' not in item['text']
        return {'claim_0_0': '上限為 ' + token + ' 次嘗試。'}
    localized = localize_block(response, context, bundle, translate)
    claim = next(iter(project_block(context, bundle, 'synthetic', localized).concepts.values()))['claims'][0]
    assert claim['text'] == '上限為 8 次嘗試。' and claim['source_spans'][0]['quote'] == quote
    assert len(requests) == 1


def test_review_and_consolidation_keep_short_quotes_with_full_context():
    doc = document('重試上限為 8 次。等待時間為 5 秒。', [('最多重試 8 次。', '重試上限為 8 次。')])
    before = deepcopy(doc)
    view, units = review_inputs(doc)
    unit = units[0]
    assert unit.payload['claims'][0]['quotes'][0]['q'] == '重試上限為 8 次。'
    assert unit.payload['evidence'][0]['text'].endswith('5 秒。')
    reviewed, _ = apply_review(doc, view, unit, proposal(unit, meaning='重試最多 8 次。'))
    payload, _ = consolidation.pack(reviewed, [0])
    value = keep(payload)
    value['concepts'][0]['claims'][0]['text'] = '上限是重試 8 次。'
    result, _ = consolidation.apply(reviewed, consolidation.project(reviewed, [0], value))
    assert result['concepts'][0]['claims'][0]['source_spans'] == doc['concepts'][0]['claims'][0]['source_spans']
    assert result['evidence'] == before['evidence'] and result['input_binding'] == before['input_binding']
    assert validate_knowledge_structure(result) and doc == before


def test_uncertain_transcript_cannot_be_corrected_by_review_or_consolidation(tmp_path, monkeypatch):
    doc = document('此步驟叫聚盒。', [('此步驟叫聚盒，逐字稿用詞待核對。', '此步驟叫聚盒。')])
    view, units = review_inputs(doc)
    response = proposal(units[0], issue='uncertain', meaning='此步驟叫聚合。')
    result, projection = apply_review(doc, view, units[0], response)
    assert result['concepts'][0]['claims'] == doc['concepts'][0]['claims']
    assert result['status']['quality'] == 'needs_review'
    assert any(c['reason'] == 'TRANSCRIPT_UNCERTAIN_PRESERVED_ORIGINAL' for c in projection['blocked_changes'])
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **k: wire_proposal(response))
    def forbidden(*a, **k):
        raise AssertionError('待核對概念不得交給去重改字')
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', forbidden)
    archive = Archive(tmp_path/'review')
    archive.review_reuses = []
    reviewed = review_structure(doc, _runtime_lock(), archive, lambda: None, lambda *a: None)
    assert reviewed['concepts'][0]['claims'] == doc['concepts'][0]['claims']
    assert reviewed['status']['quality'] == 'needs_review'


def test_new_contract_rejects_old_checkpoint_without_changing_old_hashes(tmp_path):
    current = _settings(tmp_path)
    current['runtime_lock'] = _runtime_lock()
    previous = deepcopy(current)
    task = previous['runtime_lock']['material_semantics']
    task.pop('spoken_policy')
    task.update(request_schema='material-block-request/v1', response_schema='material-block-response/v1')
    before = canonical_sha256(previous['runtime_lock'])
    validate_runtime_lock(previous['runtime_lock']); validate_runtime_lock(current['runtime_lock'])
    assert not same_material_runtime(previous['runtime_lock'], runtime_binding(previous), current['runtime_lock'], runtime_binding(current))
    assert canonical_sha256(previous['runtime_lock']) == before


def test_transcript_metadata_is_bound_to_verified_mapping(monkeypatch):
    from runtime.source_transcript import transcript_metadata
    from runtime.errors import SourceError
    item = {'media_type': 'audio/mpeg', 'mapping_artifact_id': SOURCE,
            'mapping_sha256': 'a'*64, 'original_sha256': 'b'*64, 'normalized_sha256': 'c'*64}
    mapping = {'schema': 'source-mapping/v1', 'format': 'audio', 'original_sha256': 'b'*64,
               'normalized_sha256': 'c'*64, 'media': {'transcript_source': 'whisper', 'language': 'zh'}}
    @contextmanager
    def opened(owner, identity, *, session):
        yield SimpleNamespace(sha256='a'*64, file=BytesIO(json.dumps(mapping).encode()))
    monkeypatch.setattr('runtime.source_transcript.open_verified_artifact', opened)
    assert transcript_metadata(SOURCE, item, None) == TRANSCRIPT
    mapping['normalized_sha256'] = 'd'*64
    with pytest.raises(SourceError, match='SOURCE_BINDING_INVALID'):
        transcript_metadata(SOURCE, item, None)


def test_mixed_source_batches_keep_document_claims_on_the_original_contract():
    context = build_document_context([
        _page(1, [_block(1, 0, 'paragraph', '語音的上限是 8 次。')]),
        _page(2, [_block(2, 0, 'paragraph', '文件的上限是 9 次。')]),
    ], page_count=2)
    context['source_pages'] = [
        {'page': 1, 'source_id': SOURCE, 'normalized_page': 1},
        {'page': 2, 'source_id': 'document', 'normalized_page': 1},
    ]
    context['spoken_sources'] = {SOURCE: TRANSCRIPT}
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    response = {'concepts': [{'l': '不同來源', 'a': [], 'c': [
        {'m': '語音的上限是 8 次。', 's': [{'e': 0, 'q': '語音的上限是 8 次。'}]},
        {'m': '文件的上限是 9 次。', 's': [1]},
    ]}], 'relations': []}
    result = project_block(context, bundle, 'mixed', response)
    claims = next(iter(result.concepts.values()))['claims']
    assert [q['projection'] for q in claims] == [EXCERPT_PROJECTION, 'localized_meaning_v2']
    # 批次只有文件時，不可把另一批的語音 handle 塞成空 enum。
    schema = block_schema([1], excerpt_handles={0})
    assert schema['properties']['concepts']['items']['properties']['c']['items']['properties']['s']['items'] == {'type': 'integer', 'enum': [1]}


def test_review_preserves_two_nonadjacent_excerpts_in_the_same_evidence():
    text = '重試上限為 8 次。其他流程有 7 個步驟。等待時間為 5 秒。'
    context, bundle, response = prepared(text, [('重試最多 8 次，須等待 5 秒。', '重試上限為 8 次。')])
    response['concepts'][0]['c'][0]['s'].append({'e': 0, 'q': '等待時間為 5 秒。'})
    state = project_block(context, bundle, 'multi-quote', response)
    doc = build_knowledge_structure(context, state, source_sha256='1'*64,
        run_id=SOURCE, produced_at='2026-10-25T00:00:00+00:00',
        runtime_lock_sha256='2'*64, model_id='synthetic', model_revision='synthetic', semantic_calls=1, ocr_calls=0)
    view, units = review_inputs(doc)
    result, projection = apply_review(doc, view, units[0], proposal(units[0], meaning='等待 5 秒後，重試最多 8 次。'))
    assert len(projection['claim_changes']) == 1
    claim = result['concepts'][0]['claims'][0]
    assert claim['text'] == '等待 5 秒後，重試最多 8 次。'
    assert claim['source_spans'] == doc['concepts'][0]['claims'][0]['source_spans']
    assert validate_knowledge_structure(result)


@pytest.mark.parametrize('supported', [True, False])
def test_failed_quotes_are_reselected_once_without_rewriting_siblings_and_counted(supported):
    from test_independent_blocks import Archive as BlockArchive, run
    context, bundle, response = prepared(
        '重試上限為 8 次。等待時間為 5 秒。',
        [('最多重試 8 次。', '上限為 8 次重試。'), ('須等待 5 秒。', '等待時間為 5 秒。')],
    )
    calls = []
    def model(client, **kwargs):
        request = kwargs['request'];calls.append(request)
        if 'quote_corrections' in request:
            assert list(request['quote_corrections']) == ['claim_0_0']
            assert request['quote_corrections']['claim_0_0']['meaning'] == '最多重試 8 次。'
            return {'claim_0_0': [{'e': 0, 'q': '重試上限為 8 次。'}] if supported else []}
        return deepcopy(response)
    state = SemanticState();progress = {'cursor': 0, 'ranges': [], 'semantic_calls': 0};archive = BlockArchive()
    run(context, state, progress, archive, model)
    assert len(calls) == progress['semantic_calls'] == 2
    claims = next(iter(state.concepts.values()))['claims']
    assert len(claims) == (2 if supported else 1)
    assert claims[-1]['text'] == '須等待 5 秒。'
    assert state.rejected_claims == (0 if supported else 1)
    if supported:
        assert claims[0]['text'] == '最多重試 8 次。'
    def forbidden(*a, **k):raise AssertionError('已完成引用補正不可重送')
    replay = SemanticState();resumed = {'cursor': 0, 'ranges': [], 'semantic_calls': 0}
    run(context, replay, resumed, archive, forbidden)
    assert replay == state and resumed['semantic_calls'] == 2


@pytest.mark.parametrize('source,meaning', [
    ('此步驟叫聚盒。', '此步驟叫聚合。'),
    ('靜態記憶體比動態記憶體多 3 倍空間。', '靜態記憶體空間是動態記憶體的 3 倍。'),
    ('靜態記\n憶體比動態記憶體多 3 倍空間。', '靜態記憶體空間是動態記憶體的 3 倍。'),
])
def test_uncertain_review_can_only_retract_a_paraphrase_to_its_exact_quotes(source, meaning):
    doc = document(source, [(meaning, source)])
    view, units = review_inputs(doc)
    result, projection = apply_review(doc, view, units[0], proposal(units[0], issue='uncertain', meaning=source.replace('\n', '')))
    assert projection['claim_changes']
    claim = result['concepts'][0]['claims'][0]
    assert claim['text'] == ' '.join(source.split()) + '（逐字稿用詞待核對）'
    assert claim['source_spans'] == doc['concepts'][0]['claims'][0]['source_spans']
    assert result['status']['quality'] == 'needs_review' and validate_knowledge_structure(result)


@pytest.mark.parametrize('source,meaning', [
    ('The method is called qvux.', '此方法稱為 uvwx。'),
    ('There are 8 qvux modules.', 'There are 8 uvwx modules.'),
])
def test_uncertain_foreign_quote_restore_is_not_dropped_as_a_translation_failure(tmp_path, monkeypatch, source, meaning):
    doc = document(source, [(meaning, source)])
    _, units = review_inputs(doc)
    response = proposal(units[0], issue='uncertain', meaning=source)
    monkeypatch.setattr('runtime.material_review.request_semantics', lambda *a, **k: wire_proposal(response))
    archive = Archive(tmp_path/'review')
    archive.review_reuses = []
    result = review_structure(doc, _runtime_lock(), archive, lambda: None, lambda *a: None)
    assert result['concepts'][0]['claims'][0]['text'] == source + '（逐字稿用詞待核對）'
    assert result['status']['quality'] == 'needs_review'


def test_spoken_review_rules_do_not_freeze_an_unrelated_document_concept():
    from test_material_review import view as review_view, prepare, proposal as review_proposal
    from knowledge_map.material_review import project_review
    original = review_view()
    # 同批只有第一個概念來自語音，後面的純文件概念仍可沿一般審查補正。
    original['concepts'][0]['claims'][0]['evidence'][0]['transcript'] = TRANSCRIPT
    unit = prepare(original)
    response = review_proposal()
    response['assignments'][4].update(action='needs_review', issue='uncertain', target=None)
    response['claim_edits'] = [{'claim': 4, 'meaning': '效率使用8個單位的資源。', 'evidence': [4], 'reason': '原文明示。'}]
    result = project_review(original, unit, response)
    assert any(edit['original_claim_id'] == unit.claims[4]['claim_id'] for edit in result['claim_changes'])


def mixed_claim_document(*, english_pdf=False):
    """同概念包含純逐字稿、純 PDF 與混合引用，三者不能共用疑字豁免。"""
    pdf_source = '00000000-0000-4000-8000-000000000002'
    speech = '這段逐字稿把這個步驟稱為聚盒。'
    pdf = 'The operation requires waiting for 5 seconds.' if english_pdf else '操作前須等待 5 秒。'
    context = build_document_context([
        _page(1, [_block(1, 0, 'paragraph', speech)]),
        _page(2, [_block(2, 0, 'paragraph', pdf)]),
    ], page_count=2)
    context['source_pages'] = [
        {'page': 1, 'source_id': SOURCE, 'normalized_page': 1},
        {'page': 2, 'source_id': pdf_source, 'normalized_page': 1},
    ]
    context['spoken_sources'] = {SOURCE: TRANSCRIPT}
    quote = {'e': 0, 'q': speech}
    response = {'concepts': [{'l': '操作設定', 'a': [], 'c': [
        {'m': '此步驟稱為聚盒，逐字稿用詞待核對。', 's': [quote]},
        {'m': pdf if english_pdf else '操作之前須等待 5 秒。', 's': [1]},
        {'m': 'Wait for 5 seconds before the operation.' if english_pdf else '進行操作前須等候 5 秒。', 's': [1]},
        {'m': '這個步驟稱為聚盒，操作前須等候 5 秒。', 's': [quote, 1]},
    ]}], 'relations': []}
    bundle = {'evidence': context['evidence'], 'sections': context['sections']}
    state = project_block(context, bundle, 'mixed-claim-sources', response)
    doc = build_knowledge_structure(context, state, source_sha256='1'*64,
        run_id=SOURCE, produced_at='2026-10-25T00:00:00+00:00',
        runtime_lock_sha256='2'*64, model_id='synthetic', model_revision='synthetic', semantic_calls=1, ocr_calls=0)
    return doc


def test_same_concept_keeps_pdf_and_mixed_source_claim_edits_on_general_rules():
    from knowledge_map.material_review import is_spoken_claim
    from runtime.material_review import _uncertain_claim_ids
    doc = mixed_claim_document()
    view, units = review_inputs(doc)
    unit = units[0]
    assert [is_spoken_claim(q) for q in unit.claims] == [True, False, False, False]
    assert [q['transcript_only'] for q in unit.payload['claims']] == [True, False, False, False]
    response = proposal(unit, issue='uncertain', meaning='這個步驟稱為聚合。')
    response['claim_edits'] += [
        {'claim':1, 'meaning':'操作前必須等待 5 秒。', 'evidence':[1], 'reason':'PDF 支持此敘述。'},
        {'claim':3, 'meaning':'操作前須等候 5 秒；逐字稿把步驟稱為聚盒。', 'evidence':[0,1], 'reason':'依兩份來源整理。'},
    ]
    assert _uncertain_claim_ids(unit, response['assignments']) == {unit.claims[0]['claim_id']}
    result, projection = apply_review(doc, view, unit, response)
    claims = result['concepts'][0]['claims']
    assert claims[0] == doc['concepts'][0]['claims'][0]
    assert claims[1]['text'] == '操作前必須等待 5 秒。'
    assert claims[3]['text'] == '操作前須等候 5 秒；逐字稿把步驟稱為聚盒。'
    assert len(projection['claim_changes']) == 2 and validate_knowledge_structure(result)


def test_same_concept_pdf_translation_cannot_use_the_transcript_restore_exception(tmp_path, monkeypatch):
    from runtime.material_review import _checked_review
    doc = mixed_claim_document(english_pdf=True)
    _, units = review_inputs(doc)
    unit = units[0]
    response = proposal(unit, issue='uncertain')
    response['claim_edits'] = [{'claim':1, 'meaning':unit.claims[1]['evidence'][0]['quote'],
                                'evidence':[1], 'reason':'一般 PDF 翻譯仍須核對保留代號。'}]
    calls = []
    def model(*a, **kw):
        calls.append(kw['request'])
        assert any(c['claim'] == 1 and c['literals'] for c in kw['request']['language_corrections'])
        return wire_proposal(response)
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path/'review')
    result, count = _checked_review(unit, 1, _runtime_lock(), archive, None, archive.check)
    assert result['claim_edits'] == [] and count == len(calls) == 2


def test_consolidation_keeps_only_uncertain_transcript_claim_fixed_in_mixed_concept(monkeypatch):
    from runtime.material_consolidation import consolidate_structure
    from test_material_consolidation import Archive as MergeArchive, approved
    from knowledge_map.material_review import ReviewError
    doc = mixed_claim_document()
    original = deepcopy(doc)
    frozen = doc['concepts'][0]['claims'][0]
    preserved = {frozen['claim_id']}
    calls = []
    def model(*a, **kw):
        request = kw['request'];calls.append(request['operation'])
        if request['operation'] == 'consolidation_check':
            return approved(request)
        assert request['operation'] == 'consolidation_rewrite'
        assert request['preserved_claims'] == [0]
        branches = kw['response_schema']['$defs']['Claim']['anyOf']
        assert 0 not in branches[1]['properties']['claims']['items']['enum']
        value = keep(request)
        value['concepts'][0]['claims'] = [
            {'claims':[0], 'text':None},
            {'claims':[1,2], 'text':'操作前須等待 5 秒。'},
            {'claims':[3], 'text':None},
        ]
        return value
    monkeypatch.setattr('runtime.material_consolidation.request_semantics', model)
    result = consolidate_structure(doc, _runtime_lock(), MergeArchive(), lambda:None, lambda *a:None,
                                   preserved_claim_ids=preserved)
    assert calls == ['consolidation_rewrite', 'consolidation_check']
    assert len(result['concepts'][0]['claims']) == 3
    assert result['concepts'][0]['claims'][0] == frozen and doc == original
    assert validate_knowledge_structure(result)
    illegal = keep(consolidation.pack(doc, [0])[0])
    illegal['concepts'][0]['claims'][0]['text'] = '這個步驟稱為聚合。'
    with pytest.raises(ReviewError, match='CONSOLIDATION_PROTECTED_CLAIM_CHANGED'):
        consolidation.project(doc, [0], illegal, preserved_claim_ids=preserved)


@pytest.mark.parametrize('text,quote,valid', [
    ('設定時不要設為 10%。請維持原值。', '設為 10%', False),
    ('設定時不要設為 10%。請維持原值。', '不要設為 10%', False),
    ('設定時不要設為 10%。請維持原值。', '設定時不要設為 10%。', True),
    ('請維持原值；設定時不要設為 10%。', '設定時不要設為 10%', True),
    ('設定時不要設為 10%，請維持原值。', '設定時不要設為 10%', True),
    ('設定時不要 設為10% 而應保持原本數值', '設為10% 而應保持原本數值', False),
    ('設定時不要 設為10% 而應保持原本數值', '設定時不要 設為10% 而應保持原本數值', True),
    ('Do not set the value to 10% keep the original value', 'set the value to 10%', False),
    ('Do not set the value to 10% keep the original value', 'Do not set the value to 10% keep the original value', True),
])
def test_excerpt_uses_clause_boundaries_and_keeps_unpunctuated_transcripts_whole(text, quote, valid):
    from knowledge_map.claim_literals import quoted_sources
    evidence = {'e': {'exact_text':text, 'kind':'paragraph'}}
    result = quoted_sources([{'evidence_id':'e', 'quote':quote}], evidence, excerpts=True)
    assert (result is not None) is valid


@pytest.mark.parametrize('text,quote,valid', [
    ('上。請繼續閱讀下文。', '上', False),
    ('上', '上', False),
    ('上 ！ ，。', '上 ！ ，。', False),
    ('不可逆。', '不可逆。', False),
    ('不可逆轉。', '不可逆轉。', True),
    ('a b c。', 'a b c。', False),
    ('a b c d。', 'a b c d。', True),
    ('設為 10%。', '設為 10%。', True),
])
def test_excerpt_minimum_counts_four_nonspace_nonpunctuation_characters_even_for_whole_source(text, quote, valid):
    from knowledge_map.claim_literals import quoted_sources
    evidence = {'e': {'exact_text':text, 'kind':'paragraph'}}
    result = quoted_sources([{'evidence_id':'e', 'quote':quote}], evidence, excerpts=True)
    assert (result is not None) is valid


def test_general_document_whole_evidence_does_not_inherit_excerpt_minimum():
    from knowledge_map.claim_literals import quoted_sources
    evidence = {'e': {'exact_text':'上', 'kind':'heading'}}
    assert quoted_sources([{'evidence_id':'e','quote':'上'}], evidence) is not None


def test_mixed_claim_keeps_short_pdf_evidence_through_projection_review_and_consolidation():
    from knowledge_map.material_review import is_spoken_claim
    pdf_source = '00000000-0000-4000-8000-000000000002'
    context = build_document_context([
        _page(1, [_block(1, 0, 'paragraph', '等待時間為 5 秒。其他條件另列。')]),
        _page(2, [_block(2, 0, 'heading', '上')]),
    ], page_count=2)
    context['source_pages'] = [
        {'page':1,'source_id':SOURCE,'normalized_page':1},
        {'page':2,'source_id':pdf_source,'normalized_page':1},
    ]
    context['spoken_sources'] = {SOURCE:TRANSCRIPT}
    bundle = {'sections':context['sections'],'evidence':context['evidence']}
    response = {'concepts':[{'l':'設定說明','a':[],'c':[{
        'm':'等待時間為 5 秒，文件區塊標記為上。',
        's':[{'e':0,'q':'等待時間為 5 秒。'},1],
    }]}],'relations':[]}
    def no_translation(*args):raise AssertionError('中文 Claim 不需翻譯')
    response = localize_block(response,context,bundle,no_translation)
    state = project_block(context,bundle,'mixed-short-pdf',response)
    assert state.rejected_claims == 0
    doc = build_knowledge_structure(context,state,source_sha256='1'*64,
        run_id=SOURCE,produced_at='2026-10-25T00:00:00+00:00',runtime_lock_sha256='2'*64,
        model_id='synthetic',model_revision='synthetic',semantic_calls=1,ocr_calls=0)
    view,units = review_inputs(doc)
    assert not is_spoken_claim(units[0].claims[0])
    value = proposal(units[0],issue='uncertain',meaning='文件區塊標記為上；等待時間為 5 秒。')
    value['claim_edits'][0]['evidence'] = [0,1]
    reviewed,projection = apply_review(doc,view,units[0],value)
    assert projection['claim_changes']
    rewrite = keep(consolidation.pack(reviewed,[0])[0])
    rewrite['concepts'][0]['claims'][0]['text'] = '等待時間為 5 秒；文件區塊標記為上。'
    result,_ = consolidation.apply(reviewed,consolidation.project(reviewed,[0],rewrite))
    assert validate_knowledge_structure(result)
    assert [span['quote'] for span in result['concepts'][0]['claims'][0]['source_spans']] == ['等待時間為 5 秒。','上']
    # 完整引用若真正來自逐字稿，最短門檻仍須在正式來源綁定後成立。
    speech_only = deepcopy(result)
    speech_only['input_binding']['manifest']['items'][1]['transcript'] = deepcopy(TRANSCRIPT)
    speech_only['revision'] = _revision(speech_only)
    assert not validate_knowledge_structure(speech_only)

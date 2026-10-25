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
    doc['input_binding']['manifest']['items'][0]['transcript'] = deepcopy(TRANSCRIPT)
    doc['revision'] = _revision(doc)
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
    doc['input_binding']['manifest']['items'][0]['transcript'] = deepcopy(TRANSCRIPT)
    doc['revision'] = _revision(doc)
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

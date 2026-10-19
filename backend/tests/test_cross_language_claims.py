"""跨語言敘述可翻譯，技術內容與原始引用不可變；不代表真實模型品質。"""
import pytest

from knowledge_map.claim_literals import preserves_literals
from knowledge_map.semantic_projection import _project_claim
from knowledge_map.material_review import ClaimEdit, _corrected_claim
from knowledge_map.independent_blocks import block_schema


@pytest.mark.parametrize('source,meaning', [
    ('Risk-Aware Planning (RAP) uses event memory.',
     '風險感知規劃（RAP）使用事件記憶。'),
    ('Risk-triggered correction may prevent long-horizon failure propagation.',
     '風險觸發修正可能避免長時程失敗傳播。'),
    ('Correction activates for 24.5% of decisions; 61.2% of triggered interventions modify the action.',
     '24.5% 的決策觸發修正；其中 61.2% 的介入會修改動作。'),
    ('The mass is 5 kg. The transfer rate is 8 MB/s.',
     '質量為 5 kg，傳輸速率為 8 MB/s。'),
    ('Die Verzögerung beträgt 250 ms.', '延遲為 250 ms。'),
    ('Energy follows E = mc^2.', '能量遵循 E = mc^2。'),
    ('Call f(x) before using values[0].', '使用 values[0] 前先呼叫 f(x)。'),
    ('An improvement of +3.6% and +5.4% is reported.', '報告的改善為 +3.6% 與 +5.4%。'),
    ('The API parameter is `page_limit`.', 'API 參數為 page_limit。'),
])
def test_translation_survives_projection_and_review_with_exact_evidence(source, meaning):
    evidence = {'e1': {'exact_text': source, 'kind': 'paragraph'}}
    projected = _project_claim({'meaning': meaning, 'source_spans': [
        {'evidence_id': 'e1', 'quote': source},
    ]}, evidence)
    assert projected['text'] == meaning
    assert projected['projection'] == 'localized_meaning_v2'
    assert projected['source_spans'][0]['quote'] == source
    edit = ClaimEdit(claim=0, meaning=meaning, evidence=[0], reason='翻譯原文。')
    assert _corrected_claim(edit, [{'evidence_id': 'e1', 'quote': source, 'kind': 'paragraph'}]) == meaning


@pytest.mark.parametrize('source,meaning,kind', [
    ('The value is 24.5%.', '值為 24.6%。', 'paragraph'),
    ('Rate: 8 MB/s.', '速率為 8 Mb/s。', 'paragraph'),
    ('Threshold: a <= b.', '門檻為 a < b。', 'paragraph'),
    ('Call f(x).', '呼叫 f(y)。', 'paragraph'),
    ('Energy: E = mc^2.', '能量為 E = mc。', 'paragraph'),
    ("Use the literal '\\0'.", '使用字面值 "\\0"。', 'paragraph'),
    ('return x + 1;', 'return x + 2;', 'code'),
    ('loss = -log(p)', '損失函數是對數。', 'formula'),
])
def test_translation_cannot_erase_or_change_technical_content(source, meaning, kind):
    assert not preserves_literals(meaning, [{'exact_text': source, 'kind': kind}])


def test_new_analysis_requires_explicit_learning_text_instead_of_null_source_copy():
    claim = block_schema([0])['properties']['concepts']['items']['properties']['c']['items']
    assert claim['properties']['m']['type'] == 'string'


def test_fenced_code_does_not_turn_following_translated_prose_into_a_code_literal():
    source='Use:\n```\nCache-Control: max-age=3600\n```\nThis permits reuse. The `max-age` directive sets freshness.'
    meaning='設定：\n```\nCache-Control: max-age=3600\n```\n這允許重用。`max-age` 指示詞設定新鮮期限。'
    evidence={'e1':{'exact_text':source,'kind':'paragraph'}}
    result=_project_claim({'meaning':meaning,'source_spans':[{'evidence_id':'e1','quote':source}]},evidence)
    assert result['projection']=='localized_meaning_v2'
    assert not preserves_literals(meaning.replace('3600','3601'),list(evidence.values()))


def test_markdown_emphasis_is_not_multiplication_and_quoted_apostrophes_stay_together():
    from knowledge_map.claim_literals import mask_literals
    source='Compare with a *validator*. Keep *N* seconds. It does not mean "don\'t cache".'
    meaning='與驗證器比較。保留 N 秒。這不表示 "don\'t cache"。'
    assert preserves_literals(meaning,[{'exact_text':source,'kind':'paragraph'}])
    _,values=mask_literals(source)
    assert 'a *validator' not in values.values() and 'N* seconds' not in values.values()
    assert '"don\'t cache"' in values.values()
    assert not preserves_literals('a+b',[{'exact_text':'a*b','kind':'paragraph'}])


def test_named_month_can_translate_to_the_same_chinese_calendar_date_only():
    sources=[{'exact_text':'Available since July 2015.','kind':'paragraph'}]
    assert preserves_literals('自 2015 年 7 月起可用。',sources)
    assert not preserves_literals('自 2015 年 8 月起可用。',sources)
    assert not preserves_literals('2015 年可用，改善 7%。',sources)


@pytest.mark.parametrize('source,meaning', [
    ('值為 1 或 10。', '值為 10。'),
    ('值為 1.5 或 11.5。', '值為 11.5。'),
    ('質量為 5 kg 或 15 kg。', '質量為 15 kg。'),
    ('比例為 1% 或 10%。', '比例為 10%。'),
    ('值為 1e3 或 11e3。', '值為 11e3。'),
    ('值為 1 或 -1。', '值為 -1。'),
    ('值為 1,000 或 11,000。', '值為 11,000。'),
    ('值為 10。', '值為 1 或 10。'),
])
def test_complete_quantity_tokens_cannot_be_replaced_by_numeric_substrings(source, meaning):
    evidence = {'e1': {'exact_text': source, 'kind': 'paragraph'}}
    assert not preserves_literals(meaning, list(evidence.values()))
    projected = _project_claim({'meaning': meaning, 'source_spans': [
        {'evidence_id': 'e1', 'quote': source},
    ]}, evidence)
    assert projected['projection'] == 'source_literal_repair'
    assert projected['text'] == source


def test_quantity_guard_allows_repetition_removal_spacing_and_reordered_independent_sources():
    sources = [{'exact_text': 'The mass is 5 kg. The mass is 5 kg.', 'kind': 'paragraph'},
               {'exact_text': 'The rate is 10 MB/s.', 'kind': 'paragraph'}]
    assert preserves_literals('速率為 10 MB/s，質量為5kg。', sources)


def test_persisted_legacy_claims_remain_readable_but_new_projection_rejects_numeric_loss():
    from copy import deepcopy
    from test_material_consolidation import document
    from knowledge_map.structure_rules import _id, _revision
    from knowledge_map.structure_validation import validate_knowledge_structure

    original = document(['值為 1 或 10。'], ['數值'])
    assert original['concepts'][0]['claims'][0]['projection'] == 'localized_meaning_v2'
    assert validate_knowledge_structure(original)
    legacy = deepcopy(original)
    concept = legacy['concepts'][0]
    claim = concept['claims'][0]
    claim.update(text='值為 10。', projection='localized_meaning')

    def rebind():
        previous = concept['concept_id']
        claim['claim_id'] = _id('claim', {key: value for key, value in claim.items() if key != 'claim_id'})
        concept['concept_id'] = _id('concept', {
            'label': concept['label'], 'aliases': concept['aliases'],
            'claim_ids': [claim['claim_id']], 'evidence_refs': concept['evidence_refs'],
        })
        for section in legacy['document_tree']['sections']:
            section['concept_ids'] = [concept['concept_id'] if key == previous else key for key in section['concept_ids']]
        legacy['initial_learning_path'][0]['concept_id'] = concept['concept_id']
        legacy['revision'] = _revision(legacy)

    rebind()
    before = deepcopy(legacy)
    assert validate_knowledge_structure(legacy) and legacy == before
    claim['projection'] = 'localized_meaning_v2'
    rebind()
    assert not validate_knowledge_structure(legacy)
    assert original['evidence'] == legacy['evidence']

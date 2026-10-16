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
    assert projected['projection'] == 'localized_meaning'
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

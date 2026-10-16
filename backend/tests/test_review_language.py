from copy import deepcopy

import pytest

from knowledge_map.localization import POLICY
from knowledge_map.material_review import ReviewError
from runtime.material_review import _checked_review
from test_material_review import view, prepare, proposal, wire_proposal
from test_review_parallel_feasibility import Archive


@pytest.mark.parametrize('repair', [True, False])
def test_review_requires_applicable_translation_instead_of_keeping_mixed_script(tmp_path, monkeypatch, repair):
    document = view()
    document['concepts'][4]['claims'][0]['text'] = '效率 формулировка 使用8個資源單位。'
    unit = prepare(document)
    archive = Archive(tmp_path/'archive')
    requests = []
    def model(*_, **kwargs):
        requests.append(deepcopy(kwargs['request']))
        value = proposal()
        if repair and len(requests) == 2:
            template = kwargs['request']['language_corrections'][0]['text']
            value['claim_edits'] = [{'claim': 4, 'meaning': template.replace('формулировка', '敘述'),
                                    'reason': '修正混入的外語詞。', 'evidence': [4]}]
        return wire_proposal(value)
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    lock = {'material_semantics': {'localization_policy': POLICY}, 'material_review': {'prompt': 'synthetic'}}
    if repair:
        result, calls = _checked_review(unit, 1, lock, archive, None, archive.check)
        assert calls == 2 and result['claim_edits'][0]['claim'] == 4
        assert result['claim_edits'][0]['meaning'] == '效率 敘述 使用8個資源單位。'
    else:
        with pytest.raises(ReviewError, match='REVIEW_CLAIM_EDIT_INVALID'):
            _checked_review(unit, 1, lock, archive, None, archive.check)
    assert len(requests) == 2
    assert all(r['language_corrections'][0]['claim'] == 4 and
               r['language_corrections'][0]['original_evidence'] == [4] for r in requests)

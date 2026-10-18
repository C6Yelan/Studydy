from copy import deepcopy

import pytest

from knowledge_map.localization import POLICY
from knowledge_map.material_review import ReviewError
from runtime.material_review import _checked_review
from test_material_review import view, prepare, proposal, wire_proposal
from test_review_parallel_feasibility import Archive


@pytest.mark.parametrize('edit', ['corrected', 'omitted', 'invalid_literal'])
def test_review_translation_hints_keep_literals_and_allow_retaining_original(tmp_path, monkeypatch, edit):
    document = view()
    document['concepts'][4]['claims'][0]['text'] = '效率 формулировка 使用8個資源單位。'
    unit = prepare(document)
    archive = Archive(tmp_path/'archive')
    requests = []
    def model(*_, **kwargs):
        requests.append(deepcopy(kwargs['request']))
        value = proposal()
        if edit != 'omitted':
            template = kwargs['request']['language_corrections'][0]['text']
            meaning = (template.replace('формулировка', '敘述') if edit == 'corrected'
                       else '效率 敘述 使用9個資源單位。')
            value['claim_edits'] = [{'claim': 4, 'meaning': meaning,
                                    'reason': '修正混入的外語詞。', 'evidence': [4]}]
        return wire_proposal(value)
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    lock = {'material_semantics': {'localization_policy': POLICY}, 'material_review': {'prompt': 'synthetic'}}
    if edit == 'invalid_literal':
        with pytest.raises(ReviewError, match='REVIEW_CLAIM_EDIT_INVALID'):
            _checked_review(unit, 1, lock, archive, None, archive.check)
        assert len(requests) == 2
    else:
        result, calls = _checked_review(unit, 1, lock, archive, None, archive.check)
        assert calls == len(requests) == 1
        if edit == 'corrected':
            assert result['claim_edits'][0]['claim'] == 4
            assert result['claim_edits'][0]['meaning'] == '效率 敘述 使用8個資源單位。'
        else:
            assert result['claim_edits'] == []
    assert unit.claims[4]['text'] == '效率 формулировка 使用8個資源單位。'
    assert all(r['language_corrections'][0]['claim'] == 4 and
               r['language_corrections'][0]['original_evidence'] == [4] for r in requests)

"""教學歸組只留下 advisory，不得替換 canonical concept lineage。"""
from copy import deepcopy

import pytest

from knowledge_map.material_review import ReviewError, _pack_review, apply_review, combine_reviews, validate_proposal
from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, validate_knowledge_structure
from runtime.material_review import review_inputs
from structure_fixtures import build_knowledge_structure
from test_knowledge_structure_v1 import _block, _page, RUN_ID, PRODUCED_AT, MODEL_REVISION


def _fixture():
    texts = ['佇列先取出最早加入的資料。', '容器用來保存資料。', '堆疊先取出最後加入的資料。', '授課教師：某老師。']
    context = build_document_context([_page(1, [_block(1, i, 'paragraph', t) for i, t in enumerate(texts)])], page_count=1)
    state = SemanticState()
    apply_semantic_response({'concepts': [
        {'k': str(i), 'l': label, 'a': ['錯誤別名'] if i == 0 else [], 'c': [{'m': None, 's': [i]}]}
        for i, label in enumerate(['佇列', '容器', '堆疊', '講師'])
    ], 'relations': [{'s': '0', 't': '2', 'k': 'contrast', 'r': '兩者的資料取出順序相反。', 'e': [0, 2], 'c': 0.9}]},
        context=context, bundle={'sections': context['sections'], 'evidence': context['evidence']}, state=state)
    document = build_knowledge_structure(context, state, source_sha256='1' * 64, run_id=RUN_ID,
        produced_at=PRODUCED_AT, runtime_lock_sha256='0' * 64, model_id='synthetic-test',
        model_revision=MODEL_REVISION, semantic_calls=1, ocr_calls=0)
    view, units = review_inputs(document)
    unit = units[0]
    return document, view, unit, _keep(unit)


def _keep(unit):
    return {'assignments': [{'concept': c['h'], 'action': 'keep', 'target': None, 'issue': 'none',
        'reason': '保留來源中的觀念。', 'evidence': c['evidence']} for c in unit.payload['concepts']],
        'alias_edits': [], 'claim_edits': [], 'relation_edits': []}


@pytest.mark.parametrize('action', ['group', 'example'])
def test_teaching_assignment_preserves_each_concept_claim_owner_and_relation(action):
    doc, view, unit, response = _fixture()
    before = deepcopy(doc)
    response['assignments'][0].update(action=action, target=1)
    result, audit = apply_review(doc, view, unit, response)
    assert doc == before and result == doc
    assert validate_knowledge_structure(result)
    assert audit['concept_mapping'] == {c['concept_id']: c['concept_id'] for c in doc['concepts']}
    group = next(u for u in audit['learning_units'] if u['label'] == '容器')
    assert doc['concepts'][0]['concept_id'] in group['example_concept_ids' if action == 'example' else 'member_concept_ids']
    assert result['relations'] == doc['relations']


def test_examples_with_same_teaching_target_do_not_internalize_comparison():
    doc, view, unit, response = _fixture()
    for i in [0, 2]:
        response['assignments'][i].update(action='example', target=1)
    result, audit = apply_review(doc, view, unit, response)
    assert result == doc
    assert audit['internalized_relations'] == []
    assert len(audit['relations']) == 1


def test_cross_review_unit_relation_keeps_original_endpoints():
    doc, view, unit, _ = _fixture()
    left = _pack_review(view, view['concepts'][:2], unit.evidence, '第一單元')
    right = _pack_review(view, view['concepts'][2:], unit.evidence, '第二單元')
    assert left.relations == right.relations == []
    response = _keep(left)
    response['assignments'][0].update(action='group', target=1)
    combined, proposal = combine_reviews(view, [(left, response), (right, _keep(right))])
    result, audit = apply_review(doc, view, combined, proposal)
    assert result['relations'] == doc['relations']
    assert audit['relations'][0]['source_concept_id'] == doc['concepts'][0]['concept_id']
    assert audit['relations'][0]['target_concept_id'] == doc['concepts'][2]['concept_id']


@pytest.mark.parametrize('action', ['group', 'example'])
@pytest.mark.parametrize('edit_kind', ['claim', 'alias'])
def test_edits_remain_on_the_original_member_lineage(action, edit_kind):
    doc, view, unit, response = _fixture()
    response['assignments'][0].update(action=action, target=1)
    if edit_kind == 'claim':
        response['claim_edits'] = [{'claim': 0, 'meaning': '佇列取出最早加入的資料。',
            'evidence': [0], 'reason': '依來源整理語句。'}]
    else:
        response['alias_edits'] = [{'concept': 0, 'remove': ['錯誤別名'],
            'evidence': [0], 'reason': '來源不支持此別名。'}]
    result, audit = apply_review(doc, view, unit, response)
    by_label = {c['label']: c for c in result['concepts']}
    member = by_label['佇列']
    assert member['concept_id'] != doc['concepts'][0]['concept_id']
    assert len(set(audit['concept_mapping'].values())) == len(doc['concepts'])
    assert by_label['容器'] == doc['concepts'][1]
    assert result['evidence'] == doc['evidence']
    assert result['relations'][0]['source_concept_id'] == member['concept_id']
    assert result['relations'][0]['target_concept_id'] == doc['concepts'][2]['concept_id']
    assert result['relations'][0]['evidence_refs'] == doc['relations'][0]['evidence_refs']
    if edit_kind == 'claim':
        assert member['claims'][0]['text'] == '佇列取出最早加入的資料。'
    else:
        assert member['aliases'] == [] and member['claims'] == doc['concepts'][0]['claims']
    assert validate_knowledge_structure(result)


@pytest.mark.parametrize('operation', ['reverse', 'remove', 'retype'])
def test_relation_edits_do_not_use_teaching_roots(operation):
    doc, view, unit, response = _fixture()
    response['assignments'][0].update(action='group', target=1)
    response['relation_edits'] = [{'relation': 0, 'action': operation,
        'relation_type': 'application' if operation == 'retype' else None,
        'evidence': [0, 2], 'reason': '依原端點來源提出關係修正。'}]
    result, _ = apply_review(doc, view, unit, response)
    assert result['concepts'] == doc['concepts']
    if operation == 'remove':
        assert result['relations'] == []
    else:
        edge = result['relations'][0]
        a, c = doc['concepts'][0]['concept_id'], doc['concepts'][2]['concept_id']
        assert (edge['source_concept_id'], edge['target_concept_id']) == ((c, a) if operation == 'reverse' else (a, c))
        assert edge['type'] == ('application' if operation == 'retype' else 'contrast')
    assert validate_knowledge_structure(result)


def test_metadata_still_excluded_without_absorbing_other_members():
    doc, view, unit, response = _fixture()
    response['assignments'][0].update(action='group', target=1)
    response['assignments'][3].update(action='metadata', issue='author')
    result, audit = apply_review(doc, view, unit, response)
    assert result['concepts'] == doc['concepts'][:3]
    assert result['relations'] == doc['relations']
    assert result['evidence'] == doc['evidence']
    assert audit['metadata_concept_ids'] == [doc['concepts'][3]['concept_id']]


@pytest.mark.parametrize('invalid', ['unknown_target', 'wrong_source', 'metadata_target'])
def test_teaching_proposal_guards_still_apply(invalid):
    _, _, unit, response = _fixture()
    response['assignments'][0].update(action='group', target=1)
    if invalid == 'unknown_target':
        response['assignments'][0]['target'] = 99
    elif invalid == 'wrong_source':
        response['assignments'][0]['evidence'] = [1]
    else:
        response['assignments'][1].update(action='metadata', issue='layout')
    with pytest.raises(ReviewError):
        validate_proposal(unit, response)

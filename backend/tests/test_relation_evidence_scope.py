"""關係證據以實際 request 為邊界，不要求先附屬於端點 Claim。"""
from copy import deepcopy

import pytest

from knowledge_map.semantic_projection import (
    SemanticState, apply_semantic_response, build_semantic_bundles, semantic_request,
)
from knowledge_map.structure import build_document_context, validate_knowledge_structure
from knowledge_map.structure_rules import _id, _revision
from structure_fixtures import build_knowledge_structure
from test_knowledge_structure_v1 import _block, _page, RUN_ID, PRODUCED_AT, MODEL_REVISION


def _case():
    context = build_document_context([
        _page(1, [_block(1, 0, 'heading', 'Queue'),
                  _block(1, 1, 'paragraph', 'A queue removes the earliest item.')]),
        _page(2, [_block(2, 0, 'heading', 'Stack'),
                  _block(2, 1, 'paragraph', 'A stack removes the latest item.')]),
        _page(3, [_block(3, 0, 'heading', 'Removal order comparison'),
                  _block(3, 1, 'paragraph', 'Queue and Stack differ in removal order: earliest versus latest.')]),
        _page(4, [_block(4, 0, 'heading', 'Earlier source'),
                  _block(4, 1, 'paragraph', 'The saved comparison also distinguishes earliest from latest.')]),
    ], page_count=4)
    state = SemanticState()
    bundle = next(build_semantic_bundles(
        context, state=state,
        fits=lambda request: sum(len(s['evidence']) for s in request['sections']) <= 6,
    ))
    # E1=1、E2=3、E3=5；E4=7 存在於文件，卻未在本次 bundle 中。
    response = {'concepts': [
        {'k': 'queue', 'l': 'Queue', 'a': [], 'c': [{'m': None, 's': [1]}]},
        {'k': 'stack', 'l': 'Stack', 'a': [], 'c': [{'m': None, 's': [3]}]},
    ], 'relations': [{
        's': 'queue', 't': 'stack', 'k': 'contrast',
        'r': 'Their removal order differs: earliest versus latest.', 'e': [5], 'c': 0.9,
    }]}
    return context, bundle, state, response


def _document(context, state):
    return build_knowledge_structure(
        context, state, source_sha256='1' * 64, run_id=RUN_ID,
        produced_at=PRODUCED_AT, runtime_lock_sha256='0' * 64,
        model_id='synthetic-test', model_revision=MODEL_REVISION,
        semantic_calls=1, ocr_calls=0,
    )


@pytest.mark.parametrize('refs', [[5], [1, 3]], ids=['relation-specific', 'endpoint-claims'])
def test_relation_evidence_survives_projection_and_canonical_validation(refs):
    context, bundle, state, response = _case()
    response['relations'][0]['e'] = refs
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.rejected_relations == 0
    document = _document(context, state)
    assert validate_knowledge_structure(document)
    assert len(document['relations']) == 1
    relation = document['relations'][0]
    assert relation['evidence_refs'] == [context['evidence'][i]['evidence_id'] for i in refs]
    assert relation['context_refs'] == list(dict.fromkeys(
        context['evidence'][i]['section_id'] for i in refs
    ))
    if refs == [5]:
        assert relation['evidence_refs'][0] not in {
            ref for concept in document['concepts'] for ref in concept['evidence_refs']
        }
        assert relation['context_refs'][0] not in {
            ref for concept in document['concepts'] for ref in concept['section_ids']
        }


@pytest.mark.parametrize(('field', 'value'), [
    ('e', [7]), ('e', [999]), ('e', [-1]), ('e', [True]), ('e', ['5']),
    ('e', []), ('e', [5, 5]),
    ('s', 'missing'), ('t', 'missing'), ('t', 'queue'),
    ('k', 'related_to'), ('r', 'related'),
    ('c', -0.1), ('c', 1.1), ('c', True), ('c', '0.9'),
    ('c', float('nan')), ('c', float('inf')),
])
def test_invalid_relation_is_rejected_without_losing_valid_sibling(field, value):
    context, bundle, state, response = _case()
    invalid = deepcopy(response['relations'][0])
    invalid[field] = value
    response['relations'].append(invalid)
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert len(state.relations) == 1
    assert state.rejected_relations == 1


def _saved_concept(context, label):
    source = context['evidence'][7]
    return {'label': label, 'aliases': [], 'claims': [{
        'text': source['exact_text'], 'projection': 'semantic_meaning',
        'source_spans': [{'evidence_id': source['evidence_id'], 'quote': source['exact_text']}],
    }]}


@pytest.mark.parametrize(('label', 'details', 'accepted'), [
    ('Queue', True, True),
    ('Unrelated history', True, False),
    ('Queue', False, False),
])
def test_only_exposed_catalog_evidence_is_allowed_in_incremental_request(label, details, accepted):
    context, bundle, state, response = _case()
    context['incremental'] = True
    state.concepts['saved'] = _saved_concept(context, label)
    bundle['include_claim_details'] = details
    request = semantic_request(context, bundle, state)
    assert ('e' in request['existing_concepts'][0]) is accepted
    # Catalog 的舊來源可支援 relation，但不放寬「新 Claim 只能引用 current bundle」。
    response['concepts'][0]['c'].append({'m': None, 's': [7]})
    response['relations'][0]['e'] = [7]
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert len(state.relations) == int(accepted)
    assert state.rejected_relations == int(not accepted)
    assert state.rejected_claims == 1


def test_catalog_capacity_fallback_does_not_authorize_hidden_endpoint_claim():
    context, _, state, response = _case()
    state.concepts['queue'] = _saved_concept(context, 'Queue')
    bundle = next(build_semantic_bundles(
        context, state=state,
        fits=lambda request: (
            sum(len(s['evidence']) for s in request['sections']) <= 6
            and all('c' not in c for c in request['existing_concepts'])
        ),
    ))
    assert bundle['include_claim_details'] is False
    response['relations'][0]['e'] = [7]
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.relations == []
    assert state.rejected_relations == 1


def test_response_alias_cannot_make_hidden_catalog_evidence_visible_retroactively():
    context, bundle, state, response = _case()
    state.concepts['saved'] = _saved_concept(context, 'Unrelated history')
    assert 'e' not in semantic_request(context, bundle, state)['existing_concepts'][0]
    response['concepts'].append({'k': 'saved', 'l': 'Unrelated history', 'a': ['Queue'], 'c': []})
    response['relations'][0]['e'] = [7]
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.relations == []
    assert state.rejected_relations == 1


def test_non_content_evidence_is_ineligible_even_when_exposed_in_saved_catalog():
    context, bundle, state, response = _case()
    context['non_content_evidence_ids'] = [context['evidence'][7]['evidence_id']]
    state.concepts['saved'] = _saved_concept(context, 'Queue')
    assert semantic_request(context, bundle, state)['existing_concepts'][0]['e'] == [7]
    response['relations'][0]['e'] = [7]
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.relations == []
    assert state.rejected_relations == 1


def test_non_content_evidence_removed_by_bundler_cannot_support_relation():
    context, _, state, response = _case()
    context['non_content_evidence_ids'] = [context['evidence'][5]['evidence_id']]
    bundle = next(build_semantic_bundles(context, state=state, fits=lambda request: True))
    assert 5 not in {row[0] for s in semantic_request(context, bundle, state)['sections'] for row in s['evidence']}
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.relations == []
    assert state.rejected_relations == 1


def test_unknown_canonical_section_is_rejected_during_projection():
    context, bundle, state, response = _case()
    context['evidence'][5]['section_id'] = 'unknown-section'
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    assert state.relations == []
    assert state.rejected_relations == 1


@pytest.mark.parametrize(('field', 'value'), [
    ('inference_basis', 'dependency'), ('type', 'unsupported'),
    ('learner_reason', 'related'), ('confidence', 1.1),
    ('evidence_refs', ['unknown-evidence']),
    ('context_refs', ['unknown-section']),
    ('context_refs', 'endpoint-section'),
    ('evidence_refs', 'duplicate'), ('context_refs', 'duplicate'),
    ('source_concept_id', 'unknown-concept'), ('target_concept_id', 'self'),
])
def test_canonical_relation_safeguards_remain_enforced(field, value):
    context, bundle, state, response = _case()
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    document = _document(context, state)
    relation = document['relations'][0]
    if value == 'duplicate':
        value = relation[field] * 2
    elif value == 'self':
        value = relation['source_concept_id']
    elif value == 'endpoint-section':
        value = [document['concepts'][0]['section_ids'][0]]
    relation[field] = value
    relation['relation_id'] = _id('relation', {k: v for k, v in relation.items() if k != 'relation_id'})
    document['revision'] = _revision(document)
    assert not validate_knowledge_structure(document)

"""使用合成提案驗證有界補正；不呼叫模型、不改變來源驗證規則。"""

from copy import deepcopy

import pytest

from knowledge_map.material_review import ReviewError, validate_proposal
from runtime.material_review import _checked_review
from test_material_review import prepare, proposal, wire_proposal


class Archive:
    def __init__(self, root, saved=None, rejected=None):
        self.root = root
        self.saved = saved
        self.rejected = rejected
        self.writes = {}
        self.requests = []

    def load_review(self, key, *, validate_response):
        if self.rejected is not None:
            try:
                validate_response(self.rejected)
            except ReviewError:
                pass
        return self.saved

    def prepare_review_call(self, index, key, request, *, attempt=0):
        self.requests.append(deepcopy(request))
        path = self.root / f"call-{index:06d}" / (f"repair-{attempt}" if attempt else "initial")
        path.mkdir(parents=True)
        return path

    def save_review(self, name, data):
        self.writes[name] = deepcopy(data)


def run(archive, monkeypatch, responses, cancel=lambda: None):
    calls = []

    def model(client, **kwargs):
        calls.append(deepcopy(kwargs))
        return wire_proposal(responses[len(calls) - 1])

    monkeypatch.setattr("runtime.material_review.request_semantics", model)
    result = _checked_review(
        prepare(), 1, {"material_review": {"prompt": "fixture"}}, archive, None, cancel,
    )
    return result, calls


def test_missing_coverage_is_repaired_once_with_full_context_and_strict_validation(tmp_path, monkeypatch):
    invalid = proposal()
    invalid["assignments"].pop()
    original = deepcopy(invalid)
    archive = Archive(tmp_path)
    (result, count), calls = run(archive, monkeypatch, [invalid, proposal()])
    assert count == len(calls) == 2
    assert result == proposal()
    assert invalid == original
    assert calls[1]["request"]["concepts"] == calls[0]["request"]["concepts"]

    repair = calls[1]["request"]["review_correction"]
    assert repair["previous_response"] == invalid
    assert repair["duplicate_concepts"] == []
    assert repair["missing_concepts"] == [4]
    schema = calls[0]["response_schema"]
    assignments = schema["properties"]["assignments"]
    assert assignments['type'] == 'object' and assignments['additionalProperties'] is False
    assert assignments['required'] == list(assignments['properties']) == list(map(str, range(5)))
    assert all('concept' not in v['properties'] for v in schema['$defs']['Assignment']['anyOf'])
    assert archive.writes["call-000001/response"] == invalid
    assert archive.writes["call-000001-repair-01/response"] == proposal()
    assert len([key for key in archive.writes if key.startswith("cache-")]) == 1
    validate_proposal(prepare(), result)


def test_saved_invalid_response_goes_directly_to_repair(tmp_path, monkeypatch):
    invalid = proposal()
    invalid["assignments"].append(invalid["assignments"][1])
    archive = Archive(tmp_path, rejected=invalid)
    (_, count), calls = run(archive, monkeypatch, [proposal()])
    assert count == 1
    assert "review_correction" in calls[0]["request"]
    assert "call-000001/response" not in archive.writes


def test_valid_cached_review_needs_no_model(tmp_path, monkeypatch):
    archive = Archive(tmp_path, saved=proposal())
    (result, count), calls = run(archive, monkeypatch, [])
    assert result == proposal()
    assert count == 0
    assert calls == []


def test_assignment_identity_comes_from_required_key_not_response_order(tmp_path, monkeypatch):
    original = proposal()
    wire = wire_proposal(original)
    wire['assignments'] = dict(reversed(list(wire['assignments'].items())))
    archive = Archive(tmp_path)
    (result, count), calls = run(archive, monkeypatch, [wire])
    assert count == 1
    assert {row['concept']: row for row in result['assignments']} == {
        row['concept']: row for row in original['assignments']}
    assert archive.writes['call-000001/wire-response'] == wire
    validate_proposal(prepare(), result)


@pytest.mark.parametrize('kind', ['renumbered', 'embedded_concept', 'array'])
def test_invalid_wire_identity_is_rejected_without_relabelling(tmp_path, monkeypatch, kind):
    wire = wire_proposal(proposal())
    if kind == 'renumbered':
        wire['assignments']['00'] = wire['assignments'].pop('0')
    elif kind == 'embedded_concept':
        wire['assignments']['0']['concept'] = 1
    else:
        wire = proposal()
    calls = []
    def model(*args, **kwargs):
        calls.append(kwargs)
        return deepcopy(wire)
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    archive = Archive(tmp_path)
    with pytest.raises(ReviewError, match='REVIEW_RESPONSE_INVALID'):
        _checked_review(prepare(), 1, {'material_review': {'prompt': 'fixture'}}, archive, None, lambda: None)
    assert len(calls) == 2
    assert calls[1]['request']['review_correction']['previous_response'] == wire
    assert not any(name.startswith('cache-') for name in archive.writes)


@pytest.mark.parametrize('kind,code', [
    ('metadata', 'REVIEW_METADATA_REASON_INVALID'),
    ('target', 'REVIEW_TARGET_INVALID'),
    ('cycle', 'REVIEW_GROUPING_CYCLE'),
    ('evidence', 'REVIEW_EVIDENCE_INVALID'),
    ('claim', 'REVIEW_CLAIM_EDIT_INVALID'),
    ('format', 'REVIEW_RESPONSE_INVALID'),
])
def test_other_single_batch_contract_errors_repair_once(tmp_path, monkeypatch, kind, code):
    invalid = proposal()
    if kind == 'metadata':
        invalid['assignments'][0].update(action='metadata', issue='none')
    elif kind == 'target':
        invalid['assignments'][0].update(action='group', target=0)
    elif kind == 'cycle':
        invalid['assignments'][0].update(action='group', target=1)
        invalid['assignments'][1].update(action='group', target=0)
    elif kind == 'evidence':
        invalid['assignments'][0]['evidence'] = [999]
    elif kind == 'claim':
        invalid['claim_edits'] = [{'claim': 0, 'meaning': ' ', 'reason': '合成提案', 'evidence': [0]}]
    else:
        invalid = {'assignments': None}
    archive = Archive(tmp_path, rejected=invalid)
    (result, count), calls = run(archive, monkeypatch, [proposal()])
    assert result == proposal() and count == 1
    correction = calls[0]['request']['review_correction']
    assert correction['error'] == code and correction['previous_response'] == invalid
    assert correction['invalid_metadata_reasons'] == ([{'concept': 0, 'issue': 'none'}] if kind == 'metadata' else [])
    # 同一種錯誤再次出現時，不能發布、存成功快取或無限重試。
    failed_archive = Archive(tmp_path / 'again', rejected=invalid)
    with pytest.raises(ReviewError, match=code):
        run(failed_archive, monkeypatch, [invalid])
    assert len(failed_archive.requests) == 1
    assert not any(k.startswith('cache-') for k in failed_archive.writes)


def test_assignment_schema_rejects_inconsistent_actions(tmp_path, monkeypatch):
    (_, _), calls = run(Archive(tmp_path), monkeypatch, [proposal()])
    variants = calls[0]['response_schema']['$defs']['Assignment']['anyOf']
    metadata = next(v['properties'] for v in variants if v['properties']['action']['enum'] == ['metadata'])
    assert metadata['issue']['enum'] == ['author', 'layout', 'scale']
    assert metadata['target'] == {'type': 'null'}
    grouped = next(v['properties'] for v in variants if 'group' in v['properties']['action']['enum'])
    assert grouped['target'] == {'type': 'integer', 'enum': list(range(5))}


def test_null_response_is_repaired_and_never_cached_as_success(tmp_path, monkeypatch):
    archive = Archive(tmp_path)
    (result, count), calls = run(archive, monkeypatch, [None, proposal()])
    assert result == proposal() and count == 2
    assert calls[1]['request']['review_correction']['previous_response'] is None
    assert all(v == proposal() for k, v in archive.writes.items() if k.startswith('cache-'))


@pytest.mark.parametrize('saved', [False, True])
def test_alias_case_mismatch_gets_one_grounded_correction(tmp_path, monkeypatch, saved):
    invalid = proposal()
    invalid['alias_edits'] = [{'concept': 4, 'remove': ['EFFECTIVENESS'],
        'evidence': [4], 'reason': '合成別名提案'}]
    corrected = deepcopy(invalid)
    corrected['alias_edits'][0]['remove'] = ['Effectiveness']
    archive = Archive(tmp_path, rejected=invalid if saved else None)
    (result, count), calls = run(archive, monkeypatch, [corrected] if saved else [invalid, corrected])
    assert count == (1 if saved else 2)
    correction = calls[-1]['request']['review_correction']
    assert correction['error'] == 'REVIEW_ALIAS_EDIT_INVALID'
    assert correction['alias_edit_issues'] == [{
        'concept': 4, 'allowed_aliases': ['Efficiency', 'Effectiveness'],
        'unknown_removals': ['EFFECTIVENESS'], 'duplicate_removals': [], 'has_concept_support': True,
    }]
    assert correction['previous_response'] == invalid
    assert calls[-1]['request']['concepts'] == prepare().payload['concepts']
    assert result == corrected
    validate_proposal(prepare(), result)
    assert len([key for key in archive.writes if key.startswith('cache-')]) == 1


@pytest.mark.parametrize('removals,evidence', [
    (['EFFECTIVENESS'], [4]), (['Effectiveness', 'Effectiveness'], [4]),
    (['Effectiveness'], [0]),
])
def test_invalid_alias_correction_stops_without_normalizing_or_caching(tmp_path, monkeypatch, removals, evidence):
    invalid = proposal()
    invalid['alias_edits'] = [{'concept': 4, 'remove': removals,
        'evidence': evidence, 'reason': '合成錯誤提案'}]
    archive = Archive(tmp_path)
    with pytest.raises(ReviewError, match='REVIEW_ALIAS_EDIT_INVALID'):
        run(archive, monkeypatch, [invalid, invalid])
    assert len(archive.requests) == 2
    assert not any(key.startswith('cache-') for key in archive.writes)


def test_saved_wrong_concept_support_is_repaired_without_renumbering(tmp_path, monkeypatch):
    invalid = proposal()
    unit = prepare()
    owned = set(unit.payload["concepts"][0]["evidence"])
    invalid["assignments"][0]["evidence"] = [
        next(index for index in range(len(unit.evidence)) if index not in owned)
    ]
    archive = Archive(tmp_path, rejected=invalid)
    (_, count), calls = run(archive, monkeypatch, [proposal()])
    correction = calls[0]["request"]["review_correction"]
    assert count == 1
    assert correction["error"] == "REVIEW_CONCEPT_SUPPORT_INVALID"
    assert correction["unsupported_concepts"] == [0]
    assert correction["concept_source_bindings"][0] == {
        "concept": 0, "evidence": sorted(owned),
    }


def test_assignment_source_is_model_selected_and_preserves_additional_support(tmp_path, monkeypatch):
    expected = proposal()
    expected['assignments'][0]['evidence'] = [0, 4]
    archive = Archive(tmp_path)
    (actual, count), calls = run(archive, monkeypatch, [expected])
    assert count == 1 and actual == expected
    fields = calls[0]['response_schema']['properties']['assignments']['properties']
    for concept in prepare().payload['concepts']:
        field = fields[str(concept['h'])]
        assert field['required'] == ['concept_source', 'decision']
        assert field['properties']['concept_source']['enum'] == concept['evidence']
    assert archive.writes['call-000001/wire-response']['assignments']['0']['concept_source'] == 0


@pytest.mark.parametrize('kind', ['missing', 'wrong_concept'])
def test_assignment_source_is_not_filled_or_corrected_by_decoder(tmp_path, monkeypatch, kind):
    wire = wire_proposal(proposal())
    if kind == 'missing':
        del wire['assignments']['0']['concept_source']
    else:
        wire['assignments']['0']['concept_source'] = 4
    archive = Archive(tmp_path)
    code = 'REVIEW_RESPONSE_INVALID' if kind == 'missing' else 'REVIEW_CONCEPT_SUPPORT_INVALID'
    with pytest.raises(ReviewError, match=code):
        run(archive, monkeypatch, [wire, wire])
    assert len(archive.requests) == 2
    assert not any(name.startswith('cache-') for name in archive.writes)


def test_invalid_repair_stops_without_success_cache(tmp_path, monkeypatch):
    invalid = proposal()
    invalid["assignments"].pop()
    archive = Archive(tmp_path)
    with pytest.raises(ReviewError, match="COVERAGE_INVALID"):
        run(archive, monkeypatch, [invalid, invalid])
    assert len(archive.requests) == 2
    assert not any(key.startswith("cache-") for key in archive.writes)


def test_repair_does_not_relax_evidence_validation(tmp_path, monkeypatch):
    invalid = proposal()
    invalid["assignments"].pop()
    unsafe = proposal()
    unsafe["assignments"][0]["evidence"] = [999]
    archive = Archive(tmp_path)
    with pytest.raises(ReviewError, match="EVIDENCE_INVALID"):
        run(archive, monkeypatch, [invalid, unsafe])
    assert not any(key.startswith("cache-") for key in archive.writes)


def test_cancellation_before_repair_prevents_extra_request(tmp_path, monkeypatch):
    invalid = proposal()
    invalid["assignments"].pop()
    archive = Archive(tmp_path)

    def cancel():
        if archive.requests:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        run(archive, monkeypatch, [invalid], cancel)
    assert len(archive.requests) == 1


def test_relation_type_error_repairs_with_endpoint_bindings(tmp_path, monkeypatch):
    invalid = proposal()
    unit = prepare()
    relation = unit.payload['relations'][0]
    evidence = unit.payload['concepts'][relation['source']]['evidence']
    other_type = 'part_of' if relation['type'] != 'part_of' else 'prerequisite'
    invalid['relation_edits'] = [{'relation': 0, 'action': 'reverse',
        'relation_type': other_type, 'evidence': evidence, 'reason': '合成錯誤：反轉時不可偷改型別'}]
    archive = Archive(tmp_path, rejected=invalid)
    (_, count), calls = run(archive, monkeypatch, [proposal()])
    assert count == 1
    correction = calls[0]['request']['review_correction']
    assert correction['error'] == 'REVIEW_RELATION_EDIT_INVALID'
    assert correction['relation_source_bindings'][0]['type'] == relation['type']
    assert set(evidence) <= set(correction['relation_source_bindings'][0]['evidence'])


def test_oversized_correction_reports_only_prior_model_call(tmp_path, monkeypatch):
    from runtime.semantic_service import SemanticServiceError
    invalid = proposal()
    invalid['assignments'].pop()
    archive = Archive(tmp_path)
    calls = []
    def model(*args, **kwargs):
        calls.append(kwargs['request'])
        if len(calls) == 1:
            return wire_proposal(invalid)
        raise SemanticServiceError('SEMANTIC_INPUT_TOO_LARGE')
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    with pytest.raises(SemanticServiceError) as failure:
        _checked_review(prepare(), 1, {'material_review': {'prompt': 'fixture'}}, archive, None, lambda: None)
    assert failure.value.request_metadata['review_model_calls'] == 1


def test_duplicate_relation_edit_reuses_saved_rejection_for_one_grounded_correction(tmp_path, monkeypatch):
    unit = prepare()
    invalid = proposal()
    invalid['relation_edits'] = [
        {'relation': 0, 'action': 'reverse', 'relation_type': None, 'evidence': [0, 1], 'reason': 'Synthetic direction edit.'},
        {'relation': 0, 'action': 'retype', 'relation_type': 'example', 'evidence': [0, 1], 'reason': 'Synthetic type edit.'},
    ]
    archive = Archive(tmp_path, rejected=invalid)
    (_, count), calls = run(archive, monkeypatch, [proposal()])
    assert count == 1
    correction = calls[0]['request']['review_correction']
    assert correction['error'] == 'REVIEW_EDIT_ID_INVALID'
    assert correction['edit_id_issues']['relation_edits'] == {'duplicates': [0], 'unexpected': []}
    schema = calls[0]['response_schema']
    choices = schema['properties']['relation_edits']['items']['anyOf']
    assert sorted({schema['$defs'][c['properties']['relation']['$ref'].split('/')[-1]]['enum'][0]
                   for c in choices}) == list(range(len(unit.relations)))
    assert 'at most once' in schema['properties']['relation_edits']['description']


def test_relation_schema_binds_each_edit_to_its_own_sources_and_action_type(tmp_path, monkeypatch):
    unit = prepare()
    (_, _), calls = run(Archive(tmp_path), monkeypatch, [proposal()])
    schema = calls[0]['response_schema']
    choices = schema['properties']['relation_edits']['items']['anyOf']
    def resolve(value): return schema['$defs'][value['$ref'].split('/')[-1]]
    assert len(choices) == 2 * len(unit.relations)
    for relation in unit.payload['relations']:
        variants = [c['properties'] for c in choices if resolve(c['properties']['relation'])['enum'] == [relation['h']]]
        expected = sorted(set(unit.payload['concepts'][relation['source']]['evidence'] +
                              unit.payload['concepts'][relation['target']]['evidence']))
        assert all([resolve(item)['enum'][0] for item in resolve(v['evidence'])['items']['anyOf']] == expected
                   for v in variants)
        fixed = next(v for v in variants if 'remove' in resolve(v['action'])['enum'])
        assert resolve(fixed['action'])['enum'] == ['reverse', 'remove']
        assert resolve(fixed['relation_type'])['enum'] == [None, relation['type']]
        retype = next(v for v in variants if resolve(v['action'])['enum'] == ['retype'])
        assert None not in resolve(retype['relation_type'])['enum']


def test_alias_schema_binds_whole_alias_strings_to_their_concept(tmp_path, monkeypatch):
    unit = prepare()
    compound = 'Node Expansion and Fake Tool Invocation'
    unit.concepts[4]['aliases'] = [compound]
    unit.payload['concepts'][4]['aliases'] = [compound]
    result = proposal()
    result['alias_edits'] = [{'concept': 4, 'remove': [compound], 'evidence': [4, 0], 'reason': 'Synthetic compound alias correction'}]
    calls = []
    def model(client, **kwargs):
        calls.append(kwargs)
        return wire_proposal(result)
    monkeypatch.setattr('runtime.material_review.request_semantics', model)
    actual, count = _checked_review(unit, 1, {'material_review': {'prompt': 'fixture'}}, Archive(tmp_path), None, lambda: None)
    assert actual == result and count == 1
    choices = calls[0]['response_schema']['properties']['alias_edits']['items']['anyOf']
    allowed = {choice['properties']['concept']['enum'][0]: choice['properties']['remove']['items']['enum'] for choice in choices}
    assert allowed == {c['h']: list(dict.fromkeys(c['aliases'])) for c in unit.payload['concepts'] if c['aliases']}
    assert allowed[4] == [compound]
    for choice in choices:
        concept = unit.payload['concepts'][choice['properties']['concept']['enum'][0]]
        assert choice['properties']['concept_source']['enum'] == concept['evidence']
        assert 'concept_source' in choice['required']
        assert choice['properties']['evidence']['minItems'] == 0
    invalid = deepcopy(result)
    invalid['alias_edits'][0]['remove'] = ['Node Expansion', 'Fake Tool Invocation']
    with pytest.raises(ReviewError, match='REVIEW_ALIAS_EDIT_INVALID'):
        validate_proposal(unit, invalid)


def test_missing_alias_source_cannot_be_filled_from_existing_concept(tmp_path, monkeypatch):
    wire = wire_proposal(proposal())
    wire['alias_edits'] = [{'concept': 4, 'remove': ['Effectiveness'],
        'evidence': [4], 'reason': 'Synthetic missing source selection'}]
    archive = Archive(tmp_path)
    with pytest.raises(ReviewError, match='REVIEW_RESPONSE_INVALID'):
        run(archive, monkeypatch, [wire, wire])
    assert len(archive.requests) == 2
    assert not any(name.startswith('cache-') for name in archive.writes)


def test_identical_duplicate_edits_are_merged_without_correction(tmp_path, monkeypatch):
    duplicated = proposal()
    edit = {'relation': 0, 'action': 'reverse', 'relation_type': None, 'evidence': [0, 1],
            'reason': 'Synthetic direction edit.'}
    duplicated['relation_edits'] = [edit, deepcopy(edit)]
    (_, count), calls = run(Archive(tmp_path), monkeypatch, [duplicated])
    assert count == len(calls) == 1
    assert len(validate_proposal(prepare(), duplicated).relation_edits) == 1


def test_review_reason_has_length_ceiling():
    from knowledge_map.material_review import response_schema
    assert response_schema()['$defs']['RelationEdit']['properties']['reason']['maxLength'] == 1000
    long = proposal()
    long['assignments'][0]['reason'] = 'x' * 1001
    with pytest.raises(ReviewError, match='REVIEW_RESPONSE_INVALID'):
        validate_proposal(prepare(), long)

"""使用合成提案驗證有界補正；不呼叫模型、不改變來源驗證規則。"""

from copy import deepcopy

import pytest

from knowledge_map.material_review import ReviewError, validate_proposal
from runtime.material_review import _checked_review
from test_material_review import prepare, proposal


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
        return deepcopy(responses[len(calls) - 1])

    monkeypatch.setattr("runtime.material_review.request_semantics", model)
    result = _checked_review(
        prepare(), 1, {"material_review": {"prompt": "fixture"}}, archive, None, cancel,
    )
    return result, calls


@pytest.mark.parametrize("kind", ["duplicate", "missing"])
def test_coverage_is_repaired_once_with_full_context_and_strict_validation(tmp_path, monkeypatch, kind):
    invalid = proposal()
    if kind == "duplicate":
        invalid["assignments"].append({
            **invalid["assignments"][1], "action": "group", "target": 0,
        })
    else:
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
    assert repair["duplicate_concepts"] == ([1] if kind == "duplicate" else [])
    assert repair["missing_concepts"] == ([4] if kind == "missing" else [])
    schema = calls[0]["response_schema"]
    assignments = schema["properties"]["assignments"]
    assert assignments["minItems"] == assignments["maxItems"] == 5
    assert schema["$defs"]["Assignment"]["properties"]["concept"]["enum"] == list(range(5))
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


def test_invalid_repair_stops_without_success_cache(tmp_path, monkeypatch):
    invalid = proposal()
    invalid["assignments"].append(invalid["assignments"][1])
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
    invalid['assignments'].append(deepcopy(invalid['assignments'][0]))
    archive = Archive(tmp_path)
    calls = []
    def model(*args, **kwargs):
        calls.append(kwargs['request'])
        if len(calls) == 1:
            return invalid
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

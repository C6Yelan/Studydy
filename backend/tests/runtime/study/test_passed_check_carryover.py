"""使用受控回應驗證跨版本檢測承接，保留原始成績與來源。"""
from copy import deepcopy
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient

from assessment_fixtures import concept_fixture, create, finish, answer, read, supplement, change
from product_fixtures import closed_loop, publish_fixture_structure, ORIGIN, _app
from learning_adaptation import assessment_sets as sets
from learning_adaptation.learner_progress import derive_learner_progress, apply_guidance
from learning_adaptation.study_sessions import create_study_session
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.source_normalization import upload_source, normalize_next, read_sources
from runtime.source_revisions import create_revision


def append_revision(fixture, *, mutation=None):
    owner = fixture['learner'].learner_id
    material = fixture['source'].material_id
    dsn = fixture['dsn']
    old = fixture['document']
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), 'Extra topic uses NEW.')
        payload = pdf.tobytes()
    source_id = upload_source(owner, material, payload, 'Additional.pdf', 'application/pdf', str(uuid4()), dsn=dsn)
    assert normalize_next(dsn=dsn)
    source = next(s for s in read_sources(owner, material, dsn=dsn) if s['source_id'] == source_id)
    run = create_revision(owner, material, [source['normalization_id']], str(uuid4()),
                          fixture['settings'], base_revision=old['revision'], dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    document = deepcopy(old)
    document['page_count'] += 1
    evidence = deepcopy(document['evidence'][0])
    evidence.update(evidence_id='additional-evidence', exact_text='Extra topic uses NEW.',
                    page=document['page_count'], block_order=0)
    evidence['source_locator']['page'] = document['page_count']
    document['evidence'].append(evidence)
    claim = {'claim_id': 'additional-claim', 'text': evidence['exact_text'],
             'projection': 'semantic_meaning', 'evidence_refs': [evidence['evidence_id']],
             'source_spans': [{'evidence_id': evidence['evidence_id'], 'quote': evidence['exact_text']}]}
    target = next(c for c in document['concepts'] if c['label'] == 'Signals')
    if mutation == 'added_point':
        target['claims'].append(claim)
    else:
        document['concepts'].append({'concept_id': 'additional-concept', 'label': 'Extra topic',
                                     'aliases': [], 'claims': [claim]})
    if mutation == 'changed_text':
        target['claims'][0]['text'] = 'Signal 0 uses code0 for signaling.'
    elif mutation == 'ambiguous':
        duplicate = deepcopy(target)
        duplicate.update(concept_id='duplicate-concept', label='Another subject')
        document['concepts'].append(duplicate)
    for stage in ('evidence', 'semantics', 'publishing'):
        _record_progress(run.run_id, stage, document['page_count'], document['page_count'], dsn=dsn)
    publish_fixture_structure(owner, material, run.run_id, document, dsn=dsn)
    concept = next(c for c in document['concepts'] if c['label'] == 'Signals')
    study = create_study_session(fixture['learner'], material, document['revision'], str(uuid4()),
                                 current_concept_id=concept['concept_id'], dsn=dsn)
    return {**fixture, 'document': document, 'run': run, 'concept': concept, 'study': study}


def passed_fixture(closed_loop, *, count=1, remediation=False):
    fixture = concept_fixture(closed_loop, count)
    root = create(fixture)
    finish(fixture)
    answer(fixture, root, wrong={1} if remediation else set())
    if remediation:
        child = supplement(fixture, root)
        finish(fixture, 'follow-up')
        answer(fixture, child)
    assert read(fixture, root)['cycle']['outcome'] == 'passed'
    return fixture, root


def test_complete_check_and_followup_carry_to_appended_revision_and_keep_history(closed_loop, monkeypatch, tmp_path):
    old, root = passed_fixture(closed_loop, count=5, remediation=True)
    original_result = read(old, root)
    original_structure = deepcopy(old['document'])
    new = append_revision(old)
    progress = derive_learner_progress(new['learner'], new['study'].study_session_id, dsn=new['dsn'])
    state = next(s for s in progress.concept_states if s.concept_id == new['concept']['concept_id'])
    cycle, = progress.assessment_cycles
    assert cycle['concept_id'] == new['concept']['concept_id'] != old['concept']['concept_id']
    assert cycle['outcome'] == 'passed' and cycle['passed_count'] == 5
    assert cycle['remediation_passed_count'] == 1
    assert cycle['inherited_from'] == {
        'study_session_id': str(old['study'].study_session_id),
        'knowledge_structure_revision': old['document']['revision'],
        'run_id': old['document']['run_id'],
    }
    assert state.attempts == 6 and state.correct_answers == 5
    assert state.status != 'mastered' and not state.mastered_claim_ids
    assert progress.next_action.action == 'advance'
    assert progress.next_action.target_concept_id != new['concept']['concept_id']
    assert sets.list_sets(new['learner'], new['study'].study_session_id, dsn=new['dsn'])['sets'] == []
    client = TestClient(_app(new['dsn'], tmp_path, monkeypatch), base_url=ORIGIN)
    client.cookies.set('studydy_session', new['token'])
    path = (f"/v1/materials/{new['source'].material_id}/knowledge-structures/{new['document']['revision']}"
            f"/study-sessions/{new['study'].study_session_id}/resume")
    response = client.get(path, params={'run_id': new['document']['run_id']})
    assert response.status_code == 200, response.json()
    assert response.json()['progress']['assessment_cycles'][0]['inherited_from'] == cycle['inherited_from']
    assert response.json()['selected_set_id'] is None
    advanced = apply_guidance(new['learner'], new['study'].study_session_id, progress.guidance_revision, dsn=new['dsn'])
    assert advanced.current_concept_id == progress.next_action.target_concept_id
    assert read(old, root) == original_result
    assert old['document'] == original_structure


@pytest.mark.parametrize('mutation', ['added_point', 'changed_text', 'ambiguous'])
def test_changed_or_ambiguous_concept_does_not_inherit_a_whole_check(closed_loop, mutation):
    old, _ = passed_fixture(closed_loop)
    new = append_revision(old, mutation=mutation)
    progress = derive_learner_progress(new['learner'], new['study'].study_session_id, dsn=new['dsn'])
    assert progress.assessment_cycles == []
    assert progress.next_action.action == 'assess'


def test_current_revision_check_supersedes_inherited_pass(closed_loop):
    old, _ = passed_fixture(closed_loop)
    new = append_revision(old)
    root = create(new, 'new-check')
    progress = derive_learner_progress(new['learner'], new['study'].study_session_id, dsn=new['dsn'])
    cycle, = progress.assessment_cycles
    assert cycle['diagnostic_set_id'] == str(root) and cycle['outcome'] == 'in_progress'
    assert not cycle.get('inherited_from')
    assert progress.next_action.action == 'continue_set'
    finish(new, 'new-version-check')
    answer(new, root, wrong={1})
    progress = derive_learner_progress(new['learner'], new['study'].study_session_id, dsn=new['dsn'])
    assert progress.assessment_cycles[0]['outcome'] == 'needs_review'
    assert progress.next_action.action == 'remediate'


def test_partial_check_does_not_become_passed_after_append(closed_loop):
    old = concept_fixture(closed_loop, 3)
    root = create(old)
    finish(old, fail={2})
    change(old, root, 'publish-partial')
    answer(old, root)
    assert read(old, root)['cycle']['outcome'] == 'incomplete'
    new = append_revision(old)
    progress = derive_learner_progress(new['learner'], new['study'].study_session_id, dsn=new['dsn'])
    assert progress.assessment_cycles == []
    assert next(s for s in progress.concept_states if s.concept_id == new['concept']['concept_id']).attempts == 2


def test_newer_historical_unfinished_check_does_not_fall_back_to_old_pass(closed_loop):
    old, _ = passed_fixture(closed_loop)
    middle = append_revision(old)
    create(middle, 'unfinished-new-check')
    current = append_revision(middle)
    progress = derive_learner_progress(current['learner'], current['study'].study_session_id, dsn=current['dsn'])
    assert progress.assessment_cycles == []

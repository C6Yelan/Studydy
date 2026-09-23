"""錯題 → 直接補強 → 由作答投影本輪結果；只用受控回應與隔離 DB。"""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from learning_adaptation import assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events
from learning_adaptation.learner_progress import derive_learner_progress
from runtime.storage.tables import Assessment, database_session
from test_closed_loop_v1 import closed_loop
from assessment_fixtures import concept_fixture, create, read, model_for
from test_accounts import _app, ORIGIN, HEADERS


def finish(f, variant='initial', *, fail=(), calls=None):
    base = model_for(f, fail=fail, calls=calls)
    prompts = {}
    def model(client, **kwargs):
        if kwargs['task'] == 'assessment':
            result = base(client, **kwargs)
            for candidate in result['candidates']:
                old = candidate['prompt']
                candidate['prompt'] = f'{variant}: {old}'
                prompts[candidate['prompt']] = old
            return result
        request = deepcopy(kwargs['request'])
        for question in request['questions']:
            question['prompt'] = prompts[question['prompt']]
        return base(client, **{**kwargs, 'request': request})
    while work := sets.claim_set_work(dsn=f['dsn']):
        sets.execute_set_work(work, dsn=f['dsn'], semantic_call=model)


def answer(f, group_id, wrong=()):
    group = read(f, group_id)
    answers = []
    for item in group['items']:
        if not item['assessment']:
            continue
        public = item['assessment']
        with database_session(f['dsn']) as session:
            correct = session.get(Assessment, public['assessment_revision']).private_answer_document['correct_option_id']
        selected = next(option['option_id'] for option in public['options'] if option['option_id'] != correct) if item['ordinal'] in wrong else correct
        answers.append({'assessment_revision': public['assessment_revision'], 'question_id': public['question_id'], 'selected_option_id': selected})
    sets.submit_set_answers(f['learner'], f['study'].study_session_id, group_id, answers, group['set_version'], str(uuid4()), dsn=f['dsn'])


def change(f, group_id, action):
    current = read(f, group_id)
    sets.change_set(f['learner'], f['study'].study_session_id, group_id, action, current['set_version'], str(uuid4()), dsn=f['dsn'])


def supplement(f, group_id, key=None):
    cycle = read(f, group_id)['cycle']
    return sets.create_remediation(f['learner'], f['study'].study_session_id, group_id,
        cycle['set_version'], key or str(uuid4()), f['settings'], dsn=f['dsn'])


def test_current_wrong_points_directly_form_group_and_retry_only_remaining_errors(closed_loop):
    f = concept_fixture(closed_loop, 4)
    root = create(f); finish(f); answer(f, root, wrong={2, 4})
    cycle = read(f, root)['cycle']
    assert cycle['pending_count'] == cycle['passed_count'] == 2
    assert cycle['outcome'] == 'needs_review' and cycle['can_create_remediation']
    assert derive_learner_progress(f['learner'], f['study'].study_session_id, dsn=f['dsn']).next_action.action == 'remediate'
    wrong = {p['claim_id'] for p in cycle['points'] if p['result'] == 'needs_review'}
    before = read_answer_events(f['learner'], f['study'].study_session_id, dsn=f['dsn'])
    child = supplement(f, root)
    assert read_answer_events(f['learner'], f['study'].study_session_id, dsn=f['dsn']) == before
    assert read(f, child)['selection_policy'] == 'needs-review-points/v1'
    assert {row['target_claim_id'] for row in read(f, child)['items']} == wrong
    assert derive_learner_progress(f['learner'], f['study'].study_session_id, dsn=f['dsn']).next_action.action == 'continue_set'
    with pytest.raises(sets.AssessmentSetError, match='CONFLICT'):
        supplement(f, root)
    finish(f, 'first supplement'); answer(f, child, wrong={2})
    cycle = read(f, root)['cycle']
    assert cycle['pending_count'] == cycle['remediation_passed_count'] == 1
    assert cycle['can_create_remediation'] and cycle['outcome'] == 'needs_review'
    assert sets.claim_set_work(dsn=f['dsn']) is None
    remaining = {p['claim_id'] for p in cycle['points'] if p['result'] == 'needs_review'}
    child2 = supplement(f, root)
    assert {row['target_claim_id'] for row in read(f, child2)['items']} == remaining
    finish(f, 'second supplement'); answer(f, child2)
    cycle = read(f, root)['cycle']
    assert cycle['outcome'] == 'passed' and cycle['pending_count'] == 0
    assert cycle['passed_count'] == 4 and cycle['remediation_passed_count'] == 2
    progress = derive_learner_progress(f['learner'], f['study'].study_session_id, dsn=f['dsn'])
    state = next(row for row in progress.concept_states if row.concept_id == f['concept']['concept_id'])
    assert state.status != 'mastered' and state.qualified_correct_items == 2
    assert progress.next_action.action in ('advance', 'complete')
    snapshot = read(f, root)
    assert read(f, root) == snapshot and sets.claim_set_work(dsn=f['dsn']) is None


def test_wrong_again_remains_pending_on_reload_without_automatic_generation(closed_loop):
    f = concept_fixture(closed_loop, 1); root = create(f); finish(f); answer(f, root, wrong={1})
    child = supplement(f, root); finish(f, 'remediation'); answer(f, child, wrong={1})
    cycle = read(f, root)['cycle']
    assert cycle['outcome'] == 'needs_review' and cycle['can_create_remediation']
    assert cycle['points'][0]['result'] == 'needs_review'
    assert read(f, root)['cycle'] == cycle
    assert sets.claim_set_work(dsn=f['dsn']) is None
    assert len(read_answer_events(f['learner'], f['study'].study_session_id, dsn=f['dsn'])) == 2


def test_assisted_correct_does_not_restore_mastery_after_latest_independent_error(closed_loop):
    f = concept_fixture(closed_loop, 1)
    for index in range(3):
        group = create(f, f'round-{index}'); finish(f, f'round-{index}')
        answer(f, group, wrong={1} if index == 2 else set())
    claim = read(f, group)['cycle']['points'][0]['claim_id']
    child = supplement(f, group); finish(f, 'assisted'); answer(f, child)
    progress = derive_learner_progress(f['learner'], f['study'].study_session_id, dsn=f['dsn'])
    state = next(row for row in progress.concept_states if row.concept_id == f['concept']['concept_id'])
    assert state.status == 'learning' and state.qualified_correct_items == 2 and not state.mastered_claim_ids
    events = read_answer_events(f['learner'], f['study'].study_session_id, dsn=f['dsn'])
    assert events[-1].assisted and events[-1].is_correct
    assert all(not event.assisted for event in events[:-1])


def test_direct_remediation_http_replay_is_scoped_and_get_does_not_create(closed_loop, tmp_path, monkeypatch):
    f=concept_fixture(closed_loop,1);root=create(f);finish(f);answer(f,root,wrong={1})
    client=TestClient(_app(f['dsn'],tmp_path,monkeypatch),base_url=ORIGIN);client.cookies.set('studydy_session',f['token'])
    url=f'/v1/study-sessions/{f["study"].study_session_id}/assessment-sets/{root}'
    before=client.get(url).json()
    body={'schema':'assessment-set-action/v1','expected_set_version':before['cycle']['set_version']}
    headers={**HEADERS,'Idempotency-Key':'remediation-http'}
    created=client.post(url+'/remediation',headers=headers,json=body)
    assert created.status_code==202,created.json()
    replay=client.post(url+'/remediation',headers=headers,json=body)
    assert replay.json()['set_id']==created.json()['set_id']
    assert replay.json()['diagnostic_set_id']==str(root) and replay.json()['kind']=='remediation'
    assert client.get(url).json()['cycle']['active_set_id']==created.json()['set_id']
    assert client.post(url+'/remediation',headers={**HEADERS,'Idempotency-Key':'duplicate'},json=body).status_code==409
    assert client.post(url+'/remediation',headers=headers,json={**body,'expected_set_version':1}).status_code==409
    assert 'runtime_lock_document' not in replay.json()
    operations={route:methods for route,methods in client.app.openapi()['paths'].items() if '/assessment-sets/' in route}
    assert {path.rsplit('/',1)[-1] for path in operations}=={'{set_id}','submissions','remediation','retry','publish-partial'}
    assert created.json()['schema']=='assessment-set/v3'
    schema=client.app.openapi()
    for name,version in [('AssessmentSetView','assessment-set/v3'),('LearnerProgressView','learner-progress/v4'),('StudyResumeView','study-resume/v5')]:
        assert schema['components']['schemas'][name]['properties']['schema']['const']==version
    for suffix in ('retry','publish-partial','remediation','submissions'):
        path=f'/v1/study-sessions/{{study_session_id}}/assessment-sets/{{set_id}}/{suffix}'
        assert any(p['name']=='Idempotency-Key' and p['required'] for p in schema['paths'][path]['post']['parameters'])


def test_partial_initial_and_failed_remediation_never_turn_unavailable_into_pass_or_wrong(closed_loop):
    f=concept_fixture(closed_loop,3);root=create(f);finish(f,fail={2});change(f,root,'publish-partial')
    answer(f,root,wrong={2})
    cycle=read(f,root)['cycle'];assert cycle['unavailable_count']==1 and cycle['pending_count']==1
    claim=next(point['claim_id'] for point in cycle['points'] if point['result']=='needs_review')
    child=supplement(f,root)
    version=read(f,root)['cycle']['set_version']
    finish(f,'failed-remediation',fail={1})
    assert read(f,root)['cycle']['set_version']>version
    assert read(f,child)['status']=='failed'
    assert len(read_answer_events(f['learner'],f['study'].study_session_id,dsn=f['dsn']))==2
    assert read(f,root)['cycle']['pending_count']==1
    change(f,child,'retry');finish(f,'retry-remediation');answer(f,child)
    cycle=read(f,root)['cycle']
    assert cycle['outcome']=='incomplete' and cycle['passed_count']==2 and cycle['unavailable_count']==1
    assert derive_learner_progress(f['learner'], f['study'].study_session_id, dsn=f['dsn']).next_action.action in ('advance', 'complete')


def test_material_removal_purges_diagnostic_and_remediation_and_fences_late_result(closed_loop):
    from runtime.material_discard import request_material_discard
    from runtime.storage.tables import AssessmentSet
    f=concept_fixture(closed_loop,1);root=create(f);finish(f);answer(f,root,wrong={1})
    child=supplement(f,root)
    work=sets.claim_set_work(dsn=f['dsn'])
    assert request_material_discard(f['learner'].learner_id,f['source'].material_id,dsn=f['dsn'])=='removed'
    calls=[];sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model_for(f,calls=calls))
    assert calls==[]
    with database_session(f['dsn']) as session:
        assert session.get(AssessmentSet,root) is None and session.get(AssessmentSet,child) is None


def test_unanswered_is_not_wrong_and_incomplete_can_advance(closed_loop):
    from runtime.storage.tables import AssessmentSet
    from learning_adaptation import answer_events
    from test_assessment_set_submission import answers_for
    f=concept_fixture(closed_loop,2);root=create(f);finish(f)
    answers=answers_for(f,root)
    with database_session(f['dsn']) as session:
        study,_,_=sets._scope(session,f['learner'],f['study'].study_session_id,lock=True)
        row=session.get(Assessment,answers[0]['assessment_revision'])
        answer_events.record_answer(session,study,row,answers[0]['selected_option_id'],'saved-answer')
        group=session.get(AssessmentSet,root);group.status='completed';group.completed_at=sets._now()
    cycle=read(f,root)['cycle']
    assert cycle['outcome']=='incomplete' and cycle['unanswered_count']==1
    assert cycle['pending_count']==0 and not cycle['can_create_remediation']
    assert derive_learner_progress(f['learner'],f['study'].study_session_id,dsn=f['dsn']).next_action.action in ('advance','complete')
    with pytest.raises(sets.AssessmentSetError,match='CONFLICT'):supplement(f,root)

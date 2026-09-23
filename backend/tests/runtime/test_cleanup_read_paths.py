"""Measure reader SELECT growth on disposable PostgreSQL; model replies are synthetic."""
from collections import Counter
from contextlib import contextmanager
import re
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from learning_adaptation import answer_events, assessment_sets as sets
from learning_adaptation.assessments import AssessmentError
from runtime.storage.tables import Assessment, database_session
from test_closed_loop_v1 import closed_loop
import assessment_fixtures as fixtures


@contextmanager
def _select_counts():
    counts = Counter()

    def counted(_connection, _cursor, statement, _parameters, _context, _many):
        if not statement.lstrip().upper().startswith('SELECT'):
            return
        for table in ('assessments', 'assessment_sets', 'assessment_set_items', 'answer_events'):
            if re.search(r'\b(?:FROM|JOIN)\s+' + table + r'\b', statement, re.IGNORECASE):
                counts[table] += 1

    event.listen(Engine, 'before_cursor_execute', counted)
    try:
        yield counts
    finally:
        event.remove(Engine, 'before_cursor_execute', counted)


def _submit(f, set_id, *, wrong=False):
    group = fixtures.read(f, set_id)
    answers = []
    with database_session(f['dsn']) as db:
        for item in group['items']:
            public = item['assessment']
            if public is None:
                continue
            row = db.get(Assessment, public['assessment_revision'])
            selected = row.private_answer_document['correct_option_id']
            if wrong:
                selected = next(option['option_id'] for option in public['options'] if option['option_id'] != selected)
            answers.append({'assessment_revision': public['assessment_revision'],
                            'question_id': public['question_id'], 'selected_option_id': selected})
    sets.submit_set_answers(f['learner'], f['study'].study_session_id, set_id, answers,
                            group['set_version'], str(uuid4()), dsn=f['dsn'])


def _measure_reader(f, reader):
    with database_session(f['dsn']) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        study, _, _ = sets._scope(db, f['learner'], f['study'].study_session_id)
        with _select_counts() as counts:
            result = reader(db, study)
    return result, counts


def test_answer_reader_assessment_selects_do_not_grow(closed_loop, monkeypatch):
    measurements = []
    checked = []
    validate = answer_events.validate_stored_assessment

    def counted(row):
        checked.append(row.assessment_revision)
        return validate(row)

    monkeypatch.setattr(answer_events, 'validate_stored_assessment', counted)
    for size in (1, 5):
        f = fixtures.concept_fixture(closed_loop, size)
        set_id = fixtures.create(f)
        fixtures.finish(f)
        _submit(f, set_id)
        checked.clear()
        result, counts = _measure_reader(f, answer_events._read_events)
        assert len(result) == size and len(checked) == size
        assert [row.event_number for row in result] == list(range(1, size + 1))
        assert all(row.is_correct and not row.assisted for row in result)
        measurements.append(counts['assessments'])
    assert measurements[0] > 0 and measurements[1] == measurements[0]


def test_answer_batch_still_rejects_invalid_private_document(closed_loop, monkeypatch):
    f = fixtures.concept_fixture(closed_loop, 3)
    set_id = fixtures.create(f)
    fixtures.finish(f)
    _submit(f, set_id)
    validate = answer_events.validate_stored_assessment
    checked = []

    def reject_last(row):
        checked.append(row.assessment_revision)
        if len(checked) == 3:
            raise AssessmentError('ASSESSMENT_UNAVAILABLE')
        return validate(row)

    monkeypatch.setattr(answer_events, 'validate_stored_assessment', reject_last)
    with pytest.raises(answer_events.AnswerSubmissionError, match='ANSWER_ASSESSMENT_UNAVAILABLE'):
        _measure_reader(f, answer_events._read_events)
    assert len(checked) == 3


def _many_concepts(closed_loop, monkeypatch, size):
    # Split only the synthetic semantic proposal; the real builder/publication
    # pipeline still establishes valid Concept/Claim identities and scope.
    apply = fixtures.apply_semantic_response

    def split(response, **kwargs):
        first, other = response['concepts']
        response = {**response, 'concepts': [
            {**first, 'k': f"signals_{index}",
             'l': first['l'] if index == 0 else f"Signals {index}", 'c': [claim]}
            for index, claim in enumerate(first['c'])
        ] + [other]}
        return apply(response, **kwargs)

    with monkeypatch.context() as local:
        local.setattr(fixtures, 'apply_semantic_response', split)
        f = fixtures.concept_fixture(closed_loop, size)
    concepts = [row for row in f['document']['concepts'] if row['label'].startswith('Signals')]
    assert len(concepts) == size
    ids = [sets.create_set(f['learner'], f['study'].study_session_id, row['concept_id'],
                           str(uuid4()), f['settings'], dsn=f['dsn']) for row in concepts]
    fixtures.finish(f)
    for set_id in ids:
        _submit(f, set_id)
    return f, ids


def test_set_list_member_and_answer_selects_do_not_grow(closed_loop, monkeypatch):
    measurements = []
    for size in (1, 5):
        f, ids = _many_concepts(closed_loop, monkeypatch, size)
        result, counts = _measure_reader(f, sets._list_sets)
        assert {row['set_id'] for row in result['sets']} == {str(identity) for identity in ids}
        assert all(row['published_count'] == row['answered_count'] == row['passed_count'] == 1
                   for row in result['sets'])
        assert result['active_set_ids'] == []
        measurements.append(counts)
    for table in ('assessment_sets', 'assessment_set_items', 'answer_events'):
        assert measurements[0][table] > 0 and measurements[1][table] == measurements[0][table]


def test_latest_cycle_family_selects_do_not_grow(closed_loop, monkeypatch):
    measurements = []
    for size in (1, 5):
        f, ids = _many_concepts(closed_loop, monkeypatch, size)
        result, counts = _measure_reader(f, sets._read_cycles)
        assert {row['diagnostic_set_id'] for row in result} == {str(identity) for identity in ids}
        assert all(row['outcome'] == 'passed' and row['passed_count'] == 1 for row in result)
        assert all('points' not in row and 'can_create_remediation' not in row for row in result)
        measurements.append(counts)
    for table in ('assessment_sets', 'assessment_set_items', 'answer_events'):
        assert measurements[0][table] > 0 and measurements[1][table] == measurements[0][table]


def test_cycle_keeps_latest_diagnostic_and_other_active_block(closed_loop):
    f = fixtures.concept_fixture(closed_loop, 1)
    old_id = fixtures.create(f)
    fixtures.finish(f)
    _submit(f, old_id, wrong=True)
    before = fixtures.read(f, old_id)['cycle']
    assert before['outcome'] == 'needs_review' and before['can_create_remediation']
    new_id = fixtures.create(f, str(uuid4()))
    # A new active diagnostic outside the old family blocks old remediation.
    old = fixtures.read(f, old_id)['cycle']
    assert old['outcome'] == 'needs_review' and not old['can_create_remediation']
    latest, _ = _measure_reader(f, sets._read_cycles)
    assert len(latest) == 1 and latest[0]['diagnostic_set_id'] == str(new_id)
    assert latest[0]['active_set_id'] == str(new_id)


@pytest.mark.parametrize('answered', [False, True])
def test_set_detail_selects_do_not_grow_per_member(closed_loop, answered):
    measurements = []
    for size in (1, 5):
        f = fixtures.concept_fixture(closed_loop, size)
        set_id = fixtures.create(f)
        fixtures.finish(f)
        if answered:
            _submit(f, set_id)
        with _select_counts() as counts:
            result = fixtures.read(f, set_id)
        assert result['published_count'] == size and len(result['items']) == size
        assert result['answered_count'] == (size if answered else 0)
        assert all(item['can_submit'] is (not answered) for item in result['items'])
        assert all((item['feedback'] is not None) is answered for item in result['items'])
        assert all('correct_option_id' not in item['assessment'] for item in result['items'])
        assert fixtures.read(f, set_id) == result
        measurements.append(counts)
    for table in ('assessments', 'assessment_sets', 'assessment_set_items', 'answer_events'):
        assert measurements[0][table] > 0 and measurements[1][table] == measurements[0][table]

"""真實隔離 DB：v16 章節與正文同時發布，核對全空仍保護已發布稿。"""
from copy import deepcopy

from assessment_fixtures import concept_fixture
from product_fixtures import closed_loop
from runtime import material_notes as notes
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialNotes


def model(request, *, supported=True):
    if request.get('phase') == 'select_summary':
        remaining = 5
        picks = []
        for concept in request['concepts']:
            ids = [claim['ref'] for claim in concept['claims']][:remaining]
            remaining -= len(ids)
            picks.append({'concept': concept['ref'], 'claim_ids': ids})
        return {'concepts': picks}
    if request.get('phase') == 'review_paragraph':
        return {'checks': [{'part': part, 'missing_evidence': [], 'supported': supported,
            'reason': '' if supported else '原文不支持'} for part in request['parts']],
            'coverage': [{'claim': ref, 'explained': supported,
                'note_parts': ['blocks.0'] if supported else [],
                'reason': '' if supported else '沒有解說'} for ref in request['coverage_claims']]}
    return {'paragraphs': [{'heading': concept['label'], 'level': 1,
        'blocks': [{'type': 'text', 'text': claim['text']}], 'claim_ids': [claim['claim_id']],
        'evidence_ids': claim['evidence_ids']}
        for concept in request['concepts'] for claim in concept['claims']]}


def test_published_source_metadata_survives_empty_regeneration(closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    key = (fixture['learner'].learner_id, fixture['source'].material_id, fixture['document']['revision'], 'summary')
    while notes.step(dsn=fixture['dsn'], call=model):
        pass
    first = notes.read(*key, dsn=fixture['dsn'])
    assert first['status'] == 'ready', first['error_code']
    assert first['sections'] and first['paragraphs'] and first['unorganized_concepts'] == []
    assert {pid for section in first['sections'] for pid in section['paragraph_ids']} == {p['id'] for p in first['paragraphs']}
    with database_session(fixture['dsn']) as db:
        row = db.get(MaterialNotes, key)
        saved = deepcopy(row.published_metadata)
        assert all('_explained_claim_ids' not in p for p in row.paragraphs)
    notes.generate(*key, first['generation'], dsn=fixture['dsn'])
    updating = notes.read(*key, dsn=fixture['dsn'])
    assert updating['sections'] == first['sections']
    while notes.step(dsn=fixture['dsn'], call=lambda request: model(request, supported=False)):
        pass
    result = notes.read(*key, dsn=fixture['dsn'])
    assert result['status'] == 'failed' and result['error_code'] == 'NOTE_NO_SUPPORTED_CONTENT'
    assert result['paragraphs'] == first['paragraphs'] and result['generation'] == first['generation']
    with database_session(fixture['dsn']) as db:
        assert db.get(MaterialNotes, key).published_metadata == saved


def test_first_empty_notes_publish_missing_concepts_and_can_be_read(closed_loop):
    fixture = concept_fixture(closed_loop, 2)
    key = (fixture['learner'].learner_id, fixture['source'].material_id, fixture['document']['revision'], 'summary')
    while notes.step(dsn=fixture['dsn'], call=lambda request: model(request, supported=False)):
        pass
    result = notes.read(*key, dsn=fixture['dsn'])
    assert result['status'] == 'ready', result['error_code']
    assert result['generation'] == 1 and result['content_revision'] == key[2]
    assert result['paragraphs'] == [] and result['sections']
    assert {c['concept_id'] for c in result['unorganized_concepts']} == {
        c['concept_id'] for c in fixture['document']['concepts']}

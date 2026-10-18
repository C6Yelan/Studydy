from research_fixtures import create_confirmed_research
from copy import deepcopy
from uuid import uuid4

import pymupdf
from sqlalchemy import select

from product_fixtures import closed_loop
from sources.test_source_revisions import revisions
from research_fixtures import review_provider, search_to_selection
from runtime import research
from runtime.source_normalization import normalize_next, read_sources
from runtime.storage.knowledge_structures import read_knowledge_structure, resolve_evidence_source
from runtime.storage.tables import Material, MaterialRetrievalIndex, MaterialSourceSet, SourceNormalization
from runtime.storage.database import database_session


def test_source_set_preserves_nul_in_selected_quotes(revisions, monkeypatch):
    learner, material, _, dsn, add, start, _, _, old, _ = revisions
    from runtime.source_revisions import _descriptor
    from content_identity import canonical_sha256
    identity = add('Selected.pdf', 'A queue removes the first inserted item first.')
    with database_session(dsn) as db:
        job = db.get(SourceNormalization, identity)
        original = _descriptor(db, job)
        scope = {'schema': 'source-analysis-scope/v1', 'source_sha256': original['original_sha256'],
                 'quotes': ['PDF text with \x00 and \ue000 retained'],
                 'pages': [{'page': 1, 'regions': [[0, 0, 400, 400]]}]}
    monkeypatch.setattr("runtime.source_revisions._descriptor",
                        lambda db, job: {**_descriptor(db, job), "analysis_scope": scope})
    run = start([identity], 'nul-selected-source', old['revision'])
    with database_session(dsn) as db:
        source_set = db.get(MaterialSourceSet, run.input_source_set_id)
        assert source_set.manifest['items'][-1]['analysis_scope'] == scope
        assert canonical_sha256(source_set.manifest) == source_set.digest


def test_research_only_analyzes_relevant_page_and_preserves_original_locator(revisions, monkeypatch):
    learner, material, settings, dsn, _, _, execute, _, old, requests = revisions
    with pymupdf.open() as pdf:
        for number in range(10):
            page = pdf.new_page()
            content = 'A queue removes the first inserted item first.' if number == 6 else f'Unrelated astronomy experiment on page {number + 1}.'
            page.insert_text((72, 72), content)
        data = pdf.tobytes()
    candidate = {'id': 'selected-paper', 'kind': 'paper', 'title': 'Queue reference', 'authors': 'Fixture',
        'year': 2026, 'description': 'Queue operation explanation', 'doi': None, 'url': 'https://example.org/paper',
        'download_url': 'https://example.org/paper.pdf', 'license': 'cc-by', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
        'version': 'publishedVersion', 'eligible': True, 'reason': 'fixture', 'state': 'candidate'}
    monkeypatch.setattr(research.research_sources, 'search', lambda *args, **kwargs: ([deepcopy(candidate)], None, []))
    monkeypatch.setattr(research.research_sources, 'acquire', lambda *args, **kwargs: (data, 'application/pdf', '.pdf', {'id': candidate['id']}))
    def provider(path, body):
        result = review_provider(path, body)
        if body['request']['task'] == 'read':
            result['supports'] = [{'topic': 0, 'passages': [p['index'] for p in body['request']['passages'] if 'queue' in p['text'].lower()]}]
        return result
    monkeypatch.setattr(research, 'provider', provider)
    work = create_confirmed_research(learner.learner_id, material, '補充 Queue 原理', 'selected-research', dsn=dsn)
    identity = work['research_id']
    search_to_selection(learner.learner_id, identity, dsn)
    research.action(learner.learner_id, identity, 'acquire', [candidate['id']], dsn=dsn)
    assert research.step(dsn=dsn) and normalize_next(dsn=dsn)
    for _ in range(3):
        research.step(dsn=dsn)
    assert research.read(learner.learner_id, identity, dsn=dsn)['status'] == 'ready'
    selected = next(s for s in read_sources(learner.learner_id, material, dsn=dsn) if s['origin'] == 'research')
    assert selected['page_count'] == 10 and selected['analysis_page_count'] == 1 and selected['analysis_mode'] == 'selected'
    result = research.submit(learner.learner_id, identity, settings, dsn=dsn)
    requests.clear()
    outcome = execute()
    assert outcome.status == 'succeeded', outcome.error_code
    stored = read_knowledge_structure(learner.learner_id, material, run_id=result['run_id'], dsn=dsn)
    assert stored.document['page_count'] == old['page_count'] + 1
    source = next(e for e in stored.document['evidence'] if 'queue removes' in e['exact_text'])
    location = resolve_evidence_source(learner.learner_id, material, stored.revision, source['evidence_id'], dsn=dsn)
    assert location['normalized_page'] == 7
    assert 'Unrelated astronomy experiment' not in str(requests)


def test_index_failure_cannot_publish_new_head(revisions, monkeypatch):
    learner, material, _, dsn, add, start, execute, _, old, _ = revisions
    new = add('New.pdf', 'A queue removes the first inserted item first.')
    start([new], 'index-write-failure', old['revision'])
    from runtime import retrieval
    def failed(*args, **kwargs):
        raise OSError('synthetic index write failure')
    monkeypatch.setattr(retrieval, 'publish_index', failed)
    outcome = execute()
    assert outcome.status == 'failed'
    with database_session(dsn) as db:
        assert db.get(Material, material).head_revision == old['revision']
        assert db.get(MaterialRetrievalIndex, (learner.learner_id, material, old['revision'])) is not None
        assert len(list(db.scalars(select(MaterialRetrievalIndex).where(MaterialRetrievalIndex.material_id == material)))) == 1

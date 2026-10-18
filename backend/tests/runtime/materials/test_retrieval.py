from uuid import uuid4
from copy import deepcopy

import pytest
from sqlalchemy import select, literal_column

from product_fixtures import closed_loop
from runtime import retrieval
from runtime.errors import SourceError
from runtime.storage.tables import MaterialPassage, MaterialRetrievalIndex
from runtime.storage.database import database_session


def test_published_material_has_searchable_index_and_exact_evidence(closed_loop):
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        index = db.get(MaterialRetrievalIndex, (learner.learner_id, source.material_id, structure['revision']))
        assert index and index.passage_count > 0
        found = retrieval.search(db, learner.learner_id, source.material_id, structure['revision'], ['Stack LIFO'])
        assert found and found[0]['text'] == 'A stack follows LIFO order.'
        assert found[0]['evidence']['evidence_id'] == structure['evidence'][0]['evidence_id']
        assert found[0]['evidence']['normalized_page'] == 1


def test_retrieval_never_crosses_owner_or_revision(closed_loop):
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        with pytest.raises(SourceError, match='RESOURCE_NOT_FOUND'):
            retrieval.search(db, uuid4(), source.material_id, structure['revision'], ['Stack'])
        with pytest.raises(SourceError, match='RETRIEVAL_INDEX_REQUIRED'):
            retrieval.search(db, learner.learner_id, source.material_id, 'knowledge-structure:sha256:' + '0' * 64, ['Stack'])


def test_concept_and_podcast_paths_use_same_index(closed_loop):
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        found = retrieval.search(db, learner.learner_id, source.material_id, structure['revision'], ['Stack'])
        context = retrieval.evidence_context(db, learner.learner_id, source.material_id, structure['revision'],
                                            [structure['evidence'][0]['evidence_id']])
        assert found and context and found[0]['passage_id'] == context[0]['passage_id']


def test_search_input_is_data_not_tsquery_syntax(closed_loop):
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        found = retrieval.search(db, learner.learner_id, source.material_id, structure['revision'], ["Stack'); DROP TABLE materials; --"])
        assert found
        assert db.scalar(select(MaterialPassage.position).limit(1)) == 0


def test_neighbor_context_keeps_table_header_but_not_another_source(closed_loop):
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        first = db.scalar(select(MaterialPassage).where(MaterialPassage.material_id == source.material_id))
        header = deepcopy(first.payload)
        header['text'] = header['evidence']['quote'] = 'Header: protocol A'
        first.payload = header
        for position, identity, same_source in [(1, 'table-cell', True), (2, 'other-source', False)]:
            payload = deepcopy(header)
            payload.update(position=position, passage_id=identity, evidence_id=identity,
                           text='Does not provide the same guarantee.' if same_source else 'Unrelated source text.')
            payload['evidence'].update(evidence_id=identity, quote=payload['text'], block_order=position)
            if not same_source:
                payload['source_id'] = payload['evidence']['source_id'] = str(uuid4())
            db.add(MaterialPassage(learner_id=learner.learner_id, material_id=source.material_id,
                structure_revision=structure['revision'], position=position, evidence_id=identity,
                source_id=payload['source_id'], page=1, section_id=header['section_id'], concept_ids=[],
                payload=payload, keywords=literal_column("''::tsvector")))
        db.flush()
        context = retrieval.evidence_context(db, learner.learner_id, source.material_id, structure['revision'], ['table-cell'])
        assert [p['position'] for p in context] == [0, 1]
        assert context[0]['text'] == 'Header: protocol A'


def test_later_concept_hit_keeps_its_supporting_definition(closed_loop):
    from sqlalchemy import func
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        base = db.scalar(select(MaterialPassage).where(MaterialPassage.material_id == source.material_id)).payload
        for position in range(4):
            value = deepcopy(base)
            identity = f'new-hit-{position}'
            concept = 'formula' if position < 3 else 'definition'
            text = 'zeta alpha beta gamma' if position < 3 else 'zeta '+('context '*30)
            value.update(position=100+position, passage_id=identity, evidence_id=identity,
                         text=text, hints=[], concept_ids=[concept])
            value['evidence'].update(evidence_id=identity, quote=text)
            db.add(MaterialPassage(learner_id=learner.learner_id, material_id=source.material_id,
                structure_revision=structure['revision'], position=value['position'], evidence_id=identity,
                source_id=base['source_id'], page=1, section_id=base['section_id'], concept_ids=[concept],
                payload=value, keywords=func.to_tsvector(literal_column("'simple'::regconfig"), text)))
        context = deepcopy(base)
        context.update(position=5000, passage_id='support', evidence_id='support', hints=[],
                       text='The query vector specifies the information to match.', concept_ids=['definition'])
        context['evidence'].update(evidence_id='support', quote=context['text'])
        db.add(MaterialPassage(learner_id=learner.learner_id, material_id=source.material_id,
            structure_revision=structure['revision'], position=5000, evidence_id='support',
            source_id=base['source_id'], page=1, section_id=base['section_id'], concept_ids=['definition'],
            payload=context, keywords=literal_column("''::tsvector")))
        db.flush()
        found = retrieval.search(db, learner.learner_id, source.material_id, structure['revision'],
                                 ['zeta alpha beta gamma'], limit=4)
        assert any(p['passage_id']=='support' and p['text']==context['text'] for p in found)


@pytest.mark.parametrize('queries,grouped', [(['alpha', 'alpha beta'], False), (['alpha'], True)])
def test_distinct_topic_survives_repeated_query_or_concept_hits(closed_loop, queries, grouped):
    from sqlalchemy import func
    learner, source, _, structure, dsn, _ = closed_loop
    with database_session(dsn) as db:
        base = db.scalar(select(MaterialPassage).where(MaterialPassage.material_id == source.material_id)).payload
        for position in range(9):
            text = f'alpha common item {position}' if position < 8 else 'alpha beta separate mechanism ' + 'supporting ' * 30
            concepts = (['common'] if position < 8 else ['definition']) if grouped else []
            value = deepcopy(base)
            identity = f'facet-{position}'
            value.update(position=100+position, passage_id=identity, evidence_id=identity,
                         text=text, hints=[], concept_ids=concepts)
            value['evidence'].update(evidence_id=identity, quote=text)
            db.add(MaterialPassage(learner_id=learner.learner_id, material_id=source.material_id,
                structure_revision=structure['revision'], position=value['position'], evidence_id=identity,
                source_id=base['source_id'], page=1, section_id=base['section_id'], concept_ids=concepts,
                payload=value, keywords=func.to_tsvector(literal_column("'simple'::regconfig"), text)))
        db.flush()
        found = retrieval.search(db, learner.learner_id, source.material_id, structure['revision'],
                                 queries, limit=4, neighbors=False)
        assert len(found) == 4 and any(p['passage_id'] == 'facet-8' for p in found)

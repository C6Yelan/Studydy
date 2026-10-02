import pytest

from product_fixtures import closed_loop
from test_source_revisions import revisions
from runtime import card_sets
from runtime.storage.knowledge_structures import read_knowledge_structure, resolve_evidence_source, KnowledgeStructureStoreError


def test_published_append_retains_deck_revision_and_later_releases_it(revisions):
    learner, material, _, dsn, add, start, execute, _, original, _ = revisions
    ids = [c['concept_id'] for c in original['concepts']]
    deck = card_sets.create_card_set(learner.learner_id, material, original['revision'], '固定版本', ids,
                                    'published_order', 'pin-old', dsn=dsn)
    second = add('B.pdf', 'A queue removes the first inserted element first.')
    start([second], 'append-with-deck', original['revision'])
    published = execute()
    assert published.status == 'succeeded', published.error_code
    assert read_knowledge_structure(learner.learner_id, material, revision=original['revision'], dsn=dsn).document == original
    reopened = card_sets.read_card_set(learner.learner_id, material, deck['card_set_id'], cards=True, dsn=dsn)
    assert reopened['cards']['selection']['knowledge_structure_revision'] == original['revision']
    assert reopened['cards']['selection']['concept_ids'] == ids
    evidence = reopened['cards']['cards'][0]['claims'][0]['evidence'][0]
    assert resolve_evidence_source(learner.learner_id, material, original['revision'], evidence['evidence_id'], dsn=dsn)['original_name'] == 'A.pdf'
    card_sets.delete_card_set(learner.learner_id, material, deck['card_set_id'], deck['version'], dsn=dsn)
    third = add('C.pdf', 'A tree contains a root and child nodes.')
    start([third], 'append-after-deck-delete', published.output_binding['knowledge_structure_revision'])
    assert execute().status == 'succeeded'
    with pytest.raises(KnowledgeStructureStoreError):
        read_knowledge_structure(learner.learner_id, material, revision=original['revision'], dsn=dsn)

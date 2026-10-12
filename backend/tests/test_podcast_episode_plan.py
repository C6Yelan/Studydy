"""分集保留所選來源與實際處理容量，不評分講稿風格。"""
import pytest
from runtime.podcasts import plan_episodes, PodcastError


def claims(count=4, text='必須先確認條件，再執行操作。'):
    return [{'concept_id': 'c', 'claim_id': f'c{i}', 'text': text,
             'evidence': [{'evidence_id': f'e{i}'}]} for i in range(count)]



def concept(identity, count):
    return {'concept_id': identity, 'label': identity,
            'claims': [{**c, 'claim_id': f'{identity}-{i}', 'evidence': [{'evidence_id': f'{identity}-e{i}'}]}
                       for i, c in enumerate(claims(count))]}



def test_episode_boundary_preserves_complete_concepts_instead_of_filling_slots():
    source = {'concepts': [concept('a', 20), concept('b', 20)]}
    result = plan_episodes(source, ['a', 'b'])
    assert [len(e['claims']) for e in result] == [20, 20]
    assert [{c['concept_id'] for c in e['claims']} for e in result] == [{'a'}, {'b'}]



def test_related_concepts_stay_together_when_capacity_allows():
    view = {'concepts': [concept('a', 28), concept('b', 4), concept('c', 4)],
            'relations': [{'source_concept_id': 'b', 'target_concept_id': 'c', 'type': 'prerequisite'}]}
    result = plan_episodes(view, ['a', 'b', 'c'])
    assert [[c['concept_id'] for c in e['claims']] for e in result] == [['a']*28, ['b']*4 + ['c']*4]



def test_large_concept_remains_complete_and_caps_are_hard():
    view = {'concepts': [concept('a', 65)]}
    result = plan_episodes(view, ['a'])
    assert all(len(e['claims']) <= 32 and sum(len(c['text']) for c in e['claims']) <= 2400 for e in result)
    assert [c['claim_id'] for e in result for c in e['claims']] == [c['claim_id'] for c in view['concepts'][0]['claims']]
    view['concepts'][0]['claims'][0]['text'] = '字' * 2401
    with pytest.raises(PodcastError, match='PODCAST_SOURCE_TOO_LARGE'):
        plan_episodes(view, ['a'])



def test_longer_episode_keeps_many_short_claims_in_one_concept():
    source = {'concepts': [concept('a', 29)]}
    result = plan_episodes(source, ['a'])
    assert len(result) == 1
    assert result[0]['claims'] == [{**c, 'concept_id': 'a', 'label': 'a'} for c in source['concepts'][0]['claims']]

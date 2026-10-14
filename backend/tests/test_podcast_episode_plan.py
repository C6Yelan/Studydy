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



def pages(view):
    blocks=[{'evidence_id':e['evidence_id'],'block_order':i,'text':claim['text'],'region':[0,0,10,10]}
            for c in view['concepts'] for i,claim in enumerate(c['claims']) for e in claim['evidence']]
    return {'p1':{'source_id':'fixture','source_name':'fixture.pdf','normalized_page':1,'blocks':blocks}}


def test_episode_boundary_preserves_complete_concepts_instead_of_filling_slots():
    source = {'concepts': [concept('a', 20), concept('b', 20)]}
    result = plan_episodes(source, ['b', 'a'], pages(source))
    assert [len(e['claims']) for e in result] == [20, 20]
    assert [{c['concept_id'] for c in e['claims']} for e in result] == [{'b'}, {'a'}]



def test_related_concepts_stay_together_when_capacity_allows():
    view = {'concepts': [concept('a', 28), concept('b', 4), concept('c', 4)],
            'relations': [{'source_concept_id': 'b', 'target_concept_id': 'c', 'type': 'prerequisite'}]}
    result = plan_episodes(view, ['a', 'b', 'c'], pages(view))
    assert [[c['concept_id'] for c in e['claims']] for e in result] == [['a']*28, ['b']*4 + ['c']*4]



def test_large_concept_remains_complete_and_caps_are_hard():
    view = {'concepts': [concept('a', 65)]}
    result = plan_episodes(view, ['a'], pages(view))
    assert all(len(e['claims']) <= 32 for e in result)
    assert [c['claim_id'] for e in result for c in e['claims']] == [c['claim_id'] for c in view['concepts'][0]['claims']]
    view['concepts'][0]['claims'][0]['text'] = '字' * 2401
    result = plan_episodes(view, ['a'], pages(view))
    assert result[0]['claims'][0]['text'] == '字' * 2401
    view['concepts'][0]['claims'][0]['text'] = '字' * 40000
    with pytest.raises(PodcastError, match='PODCAST_SOURCE_TOO_LARGE'):
        plan_episodes(view, ['a'], pages(view))



def test_longer_episode_keeps_many_short_claims_in_one_concept():
    source = {'concepts': [concept('a', 29)]}
    result = plan_episodes(source, ['a'], pages(source))
    assert len(result) == 1
    assert result[0]['claims'] == [{**c, 'concept_id': 'a', 'label': 'a'} for c in source['concepts'][0]['claims']]


def test_full_original_blocks_control_capacity_even_when_claim_quotes_are_short():
    view={'concepts':[concept('a',12)]};source=pages(view)
    for block in source['p1']['blocks']:block['text']='必須保留原始條件與例外。'*200
    result=plan_episodes(view,['a'],source)
    assert len(result)>1
    assert [c['claim_id'] for e in result for c in e['claims']]==[c['claim_id'] for c in view['concepts'][0]['claims']]
    for episode in result:
        ids={e['evidence_id'] for c in episode['claims'] for e in c['evidence']}
        kept={key:{**page,'blocks':[b for b in page['blocks'] if b['evidence_id'] in ids]} for key,page in source.items()}
        from runtime.semantic_service import _luna_token_count
        import json
        from runtime.podcast_script import script_input
        projected = script_input(episode['claims'], kept)
        assert _luna_token_count(json.dumps(projected['page_context'],ensure_ascii=False))<=8000
        assert _luna_token_count(json.dumps(projected,ensure_ascii=False))<=10000


def test_missing_source_blocks_are_not_silently_omitted():
    view={'concepts':[concept('a',1)]}
    with pytest.raises(PodcastError,match='SOURCE_INSUFFICIENT'):
        plan_episodes(view,['a'],{})
    view['concepts'][0]['claims']=[]
    with pytest.raises(PodcastError,match='SOURCE_INSUFFICIENT'):
        plan_episodes(view,['a'],{})

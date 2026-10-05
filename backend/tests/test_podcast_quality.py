"""模式預算與教學訊號的合成回歸，不代表真實節目品質。"""
from copy import deepcopy
import pytest
from runtime.podcast_quality import content_budget, budget_issues, teaching_signals
from runtime.podcasts import plan_episodes, PodcastError


def claims(count=4, text='必須先確認條件，再執行操作。'):
    return [{'concept_id': 'c', 'claim_id': f'c{i}', 'text': text,
             'evidence': [{'evidence_id': f'e{i}'}]} for i in range(count)]


def beats(*texts):
    return [{'turns': [{'speaker': 'host' if i % 2 == 0 else 'guest', 'text': text}
                      for i, text in enumerate(texts)]}]


def test_quick_budget_is_lower_without_dialogue_expansion_or_claim_removal():
    source = claims(); before = deepcopy(source)
    quick = content_budget(source, 'quick', 'dialogue')
    full = content_budget(source, 'full', 'dialogue')
    assert all(quick[k] < full[k] for k in quick)
    assert quick['max_characters'] == content_budget(source, 'quick', 'solo')['max_characters']
    assert source == before
    assert budget_issues(beats('字' * (quick['max_characters'] + 1)), quick)[0]['field'] == 'max_characters'
    assert not budget_issues(beats('字' * (quick['max_characters'] + 1)), full)


def test_repeated_english_source_does_not_multiply_budget():
    text = 'The two directions of a connection can be closed independently, while the other side continues sending data. '
    one = content_budget(claims(1, text), 'quick', 'solo')
    repeated = content_budget(claims(4, text), 'quick', 'solo')
    assert one['max_characters'] == repeated['max_characters']
    larger = content_budget(claims(1, text + '另一個不同條件有獨立的限制與結果。' * 20), 'quick', 'solo')
    assert larger['max_characters'] > one['max_characters']


def test_new_script_acceptance_enforces_mode_but_existing_v2_remains_readable():
    from runtime.podcasts import validate_script
    from test_podcast_beats import sample
    episode, _, _ = sample()
    turn = episode['script']['segments'][0]['turns'][0]
    for part in turn['parts']:
        part['text'] = '字' * 160
    turn['text'] = ''.join(p['text'] for p in turn['parts'])
    assert validate_script(episode['script'], episode) == episode['script']
    assert validate_script(episode['script'], episode, 'full') == episode['script']
    with pytest.raises(PodcastError, match='PODCAST_SCRIPT_INVALID'):
        validate_script(episode['script'], episode, 'quick')


def test_exact_recap_is_found_even_at_the_end_of_a_long_turn():
    statement = '第一項必要條件是保留原本的資料順序，第二項必要條件是保留所有來源的引用關係。'
    issues = teaching_signals(beats(statement, '另一個不同的限制需要另外核對。所以，' + statement))
    assert any(s['code'] == 'repeated_recap' and s['blocking'] and s['text_offset'] > 0 for s in issues)


def test_empty_confirmation_and_question_restating_the_last_answer_require_rewrite():
    issues = teaching_signals(beats('資料必須按照先進先出的順序處理。', '所以資料必須按照先進先出的順序處理嗎？', '沒錯。'))
    assert {s['code'] for s in issues if s['blocking']} == {'restated_turn', 'empty_confirmation'}
    assert next(s for s in issues if s['code'] == 'restated_turn')['turn'] == 1


def test_regular_alternation_is_evidence_for_review_not_a_standalone_rejection():
    texts = ['第一個問題提出新的限制。', '回答解釋必要的前置條件。', '再把條件套用到另一個情境。',
             '這個情境涉及不同的資料順序。', '如果條件不成立該如何判斷？', '此時來源不足以判定結果。']
    issues = teaching_signals(beats(*texts))
    assert any(s['code'] == 'regular_alternation' for s in issues)
    assert not any(s['blocking'] for s in issues)


@pytest.mark.parametrize('left,right',[
    ('這個單位寫作 MB，代表資料量。', '這個單位寫作 Mb，代表資料量。'),
    ('條件成立時，可以執行這項操作。', '條件不成立時，不可以執行這項操作。'),
    ('設定值為 100，才能符合條件。', '設定值為 200，才能符合條件。'),
])
def test_shared_terminology_does_not_hide_a_changed_unit_negation_or_number(left, right):
    assert not any(s['blocking'] for s in teaching_signals(beats(left, right)))


def concept(identity, count):
    return {'concept_id': identity, 'label': identity,
            'claims': [{**c, 'claim_id': f'{identity}-{i}', 'evidence': [{'evidence_id': f'{identity}-e{i}'}]}
                       for i, c in enumerate(claims(count))]}


def test_episode_boundary_preserves_complete_concepts_instead_of_filling_six_slots():
    source = {'concepts': [concept('a', 4), concept('b', 4)]}
    result = plan_episodes(source, ['a', 'b'])
    assert [len(e['claims']) for e in result] == [4, 4]
    assert [{c['concept_id'] for c in e['claims']} for e in result] == [{'a'}, {'b'}]


def test_related_concepts_stay_together_when_capacity_allows():
    view = {'concepts': [concept('a', 4), concept('b', 2), concept('c', 2)],
            'relations': [{'source_concept_id': 'b', 'target_concept_id': 'c', 'type': 'prerequisite'}]}
    result = plan_episodes(view, ['a', 'b', 'c'])
    assert [[c['concept_id'] for c in e['claims']] for e in result] == [['a']*4, ['b']*2 + ['c']*2]


def test_large_concept_remains_complete_and_caps_are_hard():
    view = {'concepts': [concept('a', 13)]}
    result = plan_episodes(view, ['a'])
    assert all(len(e['claims']) <= 6 and sum(len(c['text']) for c in e['claims']) <= 2400 for e in result)
    assert [c['claim_id'] for e in result for c in e['claims']] == [c['claim_id'] for c in view['concepts'][0]['claims']]
    view['concepts'][0]['claims'][0]['text'] = '字' * 2401
    with pytest.raises(PodcastError, match='PODCAST_SOURCE_TOO_LARGE'):
        plan_episodes(view, ['a'])

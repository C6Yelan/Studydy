"""統一篇幅預算與教學訊號的合成回歸，不代表真實節目品質。"""
from copy import deepcopy
import pytest
from runtime.podcast_quality import content_budget, budget_issues, teaching_signals, join_question_beats
from runtime.podcasts import plan_episodes, PodcastError


def claims(count=4, text='必須先確認條件，再執行操作。'):
    return [{'concept_id': 'c', 'claim_id': f'c{i}', 'text': text,
             'evidence': [{'evidence_id': f'e{i}'}]} for i in range(count)]


def beats(*texts):
    return [{'turns': [{'speaker': 'host' if i % 2 == 0 else 'guest', 'text': text}
                      for i, text in enumerate(texts)]}]


def test_single_budget_does_not_expand_dialogue_length_or_remove_claims():
    source = claims(); before = deepcopy(source)
    dialogue = content_budget(source, 'dialogue')
    solo = content_budget(source, 'solo')
    assert dialogue['max_characters'] == solo['max_characters']
    assert dialogue['max_beats'] == solo['max_beats']
    assert dialogue['max_turns'] > solo['max_turns']
    assert source == before
    for budget in [dialogue, solo]:
        assert budget_issues(beats('字' * (budget['max_characters'] + 1)), budget)[0]['field'] == 'max_characters'


def test_repeated_english_source_does_not_multiply_budget():
    text = 'The two directions of a connection can be closed independently, while the other side continues sending data. '
    one = content_budget(claims(1, text), 'solo')
    repeated = content_budget(claims(4, text), 'solo')
    assert one['max_characters'] == repeated['max_characters']
    larger = content_budget(claims(1, text + '另一個不同條件有獨立的限制與結果。' * 20), 'solo')
    assert larger['max_characters'] > one['max_characters']


def test_new_script_acceptance_enforces_budget_but_stored_v2_remains_readable():
    from runtime.podcasts import validate_script
    from test_podcast_beats import sample
    episode, _, _ = sample()
    turn = episode['script']['segments'][0]['turns'][0]
    for part in turn['parts']:
        part['text'] = '字' * (content_budget(episode['claims'], episode['delivery'])['max_characters'] // len(turn['parts']) + 1)
    turn['text'] = ''.join(p['text'] for p in turn['parts'])
    from runtime.podcast_script import validate
    assert validate(episode['script'], episode) == episode['script']
    with pytest.raises(PodcastError, match='PODCAST_SCRIPT_INVALID'):
        validate_script(episode['script'], episode)


def test_exact_recap_is_found_even_at_the_end_of_a_long_turn():
    statement = '第一項必要條件是保留原本的資料順序，第二項必要條件是保留所有來源的引用關係。'
    issues = teaching_signals(beats(statement, '另一個不同的限制需要另外核對。所以，' + statement))
    assert any(s['code'] == 'repeated_recap' and not s['blocking'] and s['text_offset'] > 0 for s in issues)


@pytest.mark.parametrize('reason', ['bound_source', 'turn_capacity'])
def test_question_join_keeps_source_bearing_units_and_hard_turn_capacity(reason):
    question = {'speaker':'host','parts':[{'text':'這個條件如何影響結果？','source_refs':[]}]}
    answer = {'speaker':'guest','parts':[{'text':'來源支持的解釋。','source_refs':[{'source_index':0}]}]}
    if reason == 'bound_source': question['parts'][0]['source_refs']=[{'source_index':0}]
    value=[{'title':'問題','turns':[question]}, {'title':'解釋','turns':[answer]*(12 if reason=='turn_capacity' else 1)}]
    assert join_question_beats(value)==value


def test_empty_confirmation_and_restatement_are_contextual_review_signals():
    issues = teaching_signals(beats('資料必須按照先進先出的順序處理。', '所以資料必須按照先進先出的順序處理嗎？', '沒錯。'))
    assert {s['code'] for s in issues} == {'restated_turn', 'empty_confirmation'}
    assert next(s for s in issues if s['code'] == 'restated_turn')['turn'] == 1


def test_confirmation_loop_is_detected_inside_a_longer_explanation():
    value = beats('這種資料結構是先進先出，另一種結構才是後進先出。兩者的順序不能混為一談。',
                  '所以這種資料結構不是後進先出，而是先進先出？',
                  '對，而且還需要確認資料是否為空。')
    assert any(s['code'] == 'confirmation_loop' and not s['blocking'] for s in teaching_signals(value))


def test_application_question_and_explicit_misconception_are_not_confirmation_loops():
    for question in ('所以如果改成另一種資料結構，也能維持先進先出？',
                     '所以我原本以為這是後進先出，其實應該是先進先出？'):
        value = beats('這種資料結構是先進先出，另一種結構才是後進先出。', question,
                      '對，這裡需要依照各自的限制判斷。')
        assert not any(s['code'] == 'confirmation_loop' for s in teaching_signals(value))


def test_late_inverse_question_is_flagged_for_semantic_review_without_banning_source_reuse():
    value = beats('必要條件先確認後才能執行，第二個方向仍然可以獨立繼續。',
                  '所以第二個方向就必須立刻停止嗎？', '不是，第二個方向仍然可以繼續。')
    value[0]['turns'][0]['parts'] = [{'source_refs': [{'source_index': 0}]}]
    value[0]['turns'][2]['parts'] = [{'source_refs': [{'source_index': 0}]}]
    issues = teaching_signals(value)
    assert any(s['code'] == 'question_after_coverage' and not s['blocking'] for s in issues)


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


def test_teaching_budget_leaves_room_to_explain_many_concise_concepts():
    source = [{**c, 'concept_id': f'topic-{i // 4}', 'text': f'步驟 {i} 的獨立條件與結果。'}
              for i, c in enumerate(claims(32))]
    budget = content_budget(source, 'dialogue')
    assert budget['max_characters'] >= 2500
    assert 8 <= budget['max_beats'] <= 12
    assert budget['max_turns'] >= 32
    assert content_budget(claims(1), 'dialogue')['max_characters'] < budget['max_characters']
    assert content_budget(claims(32, '大量來源內容。' * 300), 'dialogue')['max_characters'] <= 9600

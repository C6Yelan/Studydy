from copy import deepcopy

import pytest

from runtime import material_qa as qa
from runtime.errors import SourceError


def passage(identity='p0', text='A queue removes the first inserted element first.'):
    evidence = {'evidence_id': identity, 'source_name': 'synthetic.pdf', 'normalized_page': 7, 'quote': text}
    return {'passage_id': identity, 'text': text, 'hints': ['Queue', '佇列'], 'evidence': evidence, 'references': [evidence]}


def response(text='佇列採先進先出。', indices=None, queries=None, missing=''):
    return {'segments': [{'text': text, 'citations': [0] if indices is None else indices}],
            'queries': queries or [], 'missing': missing}


def checked(*values):
    return {'items': [{'index': i, 'supported': value, 'reason': '' if value else '缺少實驗結果',
                       'correction': '', 'basis': 'sources',
                       'citations':[0] if value else []} for i, value in enumerate(values)],
            'queries':[] if all(values) else ['queue experiment'],
            'missing':'' if all(values) else '缺少實驗結果'}


def fake_model(responses, calls):
    responses = iter(responses)
    def call(path, body):
        assert path == '/semantics' and body['task'] == 'material_qa'
        calls.append(deepcopy(body))
        if body['schema']['required'] == ['summary']:
            return {'summary': '曾討論 Queue 的定義，使用者追問取出順序。'}
        if body['schema']['required'] == ['queries']:
            return {'queries': [body['request']['question']]}
        return next(responses)
    return call


def test_basic_question_answers_with_small_sources_and_preserves_citation_locator():
    calls = []
    answer = qa.answer('佇列怎麼取資料？', [], None, retrieve=lambda _: [passage()],
        call=fake_model([response(), checked(True)], calls))
    assert answer['supported'] and answer['text'] == '佇列採先進先出。'
    assert answer['paragraphs'] == [{'text': '佇列採先進先出。', 'citation_ids': [answer['citations'][0]['passage_id']]}]
    assert answer['citations'][0]['evidence'][0]['normalized_page'] == 7
    assert len(calls) == 2 and max(answer['retrieval']['estimated_input_tokens']) <= qa.INPUT_TOKENS


def test_review_uses_cited_page_context_without_resending_unrelated_candidates():
    sources = [passage(f'p{i}') for i in range(20)]
    for i, source in enumerate(sources):
        source['evidence']['normalized_page'] = i // 2 + 1
    calls = []
    qa.answer('佇列怎麼取資料？', [], None, retrieve=lambda _: sources,
        call=fake_model([response(), checked(True)], calls))
    assert len(calls[0]['request']['sources']) == 20
    assert [s['index'] for s in calls[-1]['request']['sources']] == [0, 1]
    invalid = checked(True)
    invalid['items'][0]['citations'] = [19]
    with pytest.raises(SourceError, match='VOICE_ANSWER_INVALID'):
        qa.answer('佇列怎麼取資料？', [], None, retrieve=lambda _: sources,
            call=fake_model([response(), invalid], []))


def test_empty_direct_retrieval_uses_search_planning_before_answering():
    calls = []; searches = []
    respond = fake_model([response(), checked(True)], calls)
    def call(path, body):
        if body['schema']['required'] == ['queries']:
            assert body['request']['question'] == '先到的先處理是什麼？'
            return {'queries': ['FIFO queue arrival order']}
        return respond(path, body)
    def retrieve(queries):
        searches.append(queries)
        return [] if len(searches)==1 else [passage()]
    result = qa.answer('先到的先處理是什麼？', [], None, retrieve=retrieve, call=call)
    assert searches == [['先到的先處理是什麼？'], ['FIFO queue arrival order']]
    assert result['supported'] and result['retrieval']['calls'] == 3


@pytest.mark.parametrize('question', [
    '請說明佇列的順序、操作，以及滿載時應提供哪些資訊。',
    '請說明佇列的順序、操作、滿載處理。',
])
def test_enumerated_requirements_are_planned_before_direct_retrieval(question):
    calls, searches = [], []
    respond = fake_model([response(), checked(True)], calls)
    def call(path, body):
        if body['schema']['required'] == ['queries']:
            calls.append(deepcopy(body))
            return {'queries': ['queue FIFO operations', 'bounded queue full status']}
        return respond(path, body)
    def retrieve(queries):
        searches.append(queries)
        return [passage()]
    result = qa.answer(question, [], None, retrieve=retrieve, call=call)
    assert searches == [['queue FIFO operations', 'bounded queue full status']]
    assert result['supported'] and len(calls) == 3


def test_invalid_search_plan_is_rejected_before_database_lookup():
    with pytest.raises(SourceError, match='VOICE_ANSWER_INVALID'):
        qa.answer('question', [{'question':'Queue?', 'answer':'FIFO.'}], None, retrieve=lambda _: pytest.fail('Invalid plan reached retrieval'),
                  call=lambda *args: {'queries': [' ']})


def test_partial_review_failure_does_not_discard_the_supported_answer():
    calls = []
    generated = response(); generated['segments'].append({'text': '所有情況都只需一毫秒。', 'citations': [0]})
    answer = qa.answer('說明佇列與實驗速度', [], None, retrieve=lambda _: [passage()],
        call=fake_model([generated, checked(True, False)], calls))
    assert answer['supported'] and '先進先出' in answer['text']
    assert '所有情況都只需一毫秒' not in answer['text']
    assert '缺少實驗結果' in answer['text'] and len(calls) == 2


def test_retrieval_gap_uses_new_query_once_instead_of_resending_whole_material():
    calls = []; searches = []
    def retrieve(queries):
        searches.append(queries)
        return [] if len(searches) <= 2 else [passage()]
    initial = {'segments': [], 'queries': ['Queue FIFO 佇列'], 'missing': '佇列原文'}
    answer = qa.answer('先到先服務是哪種結構？', [], None, retrieve=retrieve,
        call=fake_model([initial, response(), checked(True)], calls))
    assert answer['supported'] and len(searches) == 3 and searches[2] == ['Queue FIFO 佇列']
    assert calls[1]['request']['sources'] == [] and len(calls[2]['request']['sources']) == 1


def test_gap_keeps_precise_generation_queries_alongside_review_queries():
    calls = []; searches = []
    def retrieve(queries):
        searches.append(queries)
        return [passage()] if len(searches) == 1 else [passage('additional', 'A queue processes items in arrival order.')]
    initial = response(queries=['arrival order FIFO'], missing='取出順序')
    review = checked(True)
    review.update(queries=['queue ordering'], missing='取出順序')
    result = qa.answer('如何依到達順序處理？', [], None, retrieve=retrieve,
        call=fake_model([initial, review, response(), checked(True)], calls))
    assert result['supported'] and searches[1] == ['arrival order FIFO', 'queue ordering']
    assert len(searches) == 2 and len(calls) == 4


def test_foreign_or_boolean_citation_never_passes_to_verifier():
    for index in [99, True, -1]:
        calls = []
        with pytest.raises(SourceError, match='VOICE_ANSWER_INVALID'):
            qa.answer('question', [], None, retrieve=lambda _: [passage()],
                call=fake_model([response(indices=[index])], calls))
        assert len(calls) == 1


def test_long_history_and_many_retrieved_paragraphs_stay_bounded():
    calls = []
    history = [{'question': 'Queue 是什麼？', 'answer': 'Old explanation. ' * 600} for _ in range(8)]
    paragraphs = [passage(f'p{i}', 'A queue follows FIFO. ' * 80) for i in range(200)]
    answer = qa.answer('那它怎麼取資料？', history, None, retrieve=lambda _: paragraphs,
        call=fake_model([response(), checked(True)], calls))
    assert answer['supported']
    answer_call = next(c for c in calls if 'sources' in c['request'] and 'segments' not in c['request'])
    search_call = next(c for c in calls if c['schema']['required'] == ['queries'])
    assert len(answer_call['request']['sources']) < len(paragraphs)
    assert qa._count(search_call['request']['history']) <= qa.HISTORY_TOKENS
    assert answer['retrieval']['summary_calls'] > 0
    assert max(answer['retrieval']['estimated_input_tokens']) <= qa.INPUT_TOKENS


def test_short_history_keeps_more_than_three_turns_without_compaction():
    history = [{'question': f'第 {i} 個條件', 'answer': '已討論。'} for i in range(12)]
    calls = []
    result = qa.answer('沿用第一個條件', history, None, retrieve=lambda _: [passage()],
        call=fake_model([response(), checked(True)], calls))
    assert calls[0]['request']['history']['turns'] == history
    assert result['retrieval']['summary_calls'] == 0


def test_compaction_reuses_summary_and_sends_only_new_history():
    history = [{'turn_id': str(i), 'created_at': str(i), 'question': f'條件 {i}',
                'answer': '較長的舊說明。' * 65} for i in range(7)]
    calls = []
    result = qa.answer('回到最早的條件', history, None, retrieve=lambda _: [passage()],
        call=fake_model([response(), checked(True)], calls))
    memory = result['_memory_update']
    cursor = int(memory['through_turn_id'])
    assert memory['summary'] and result['retrieval']['history_tokens'] <= qa.HISTORY_TOKENS
    new_calls = []
    followup = qa.answer('繼續', history[cursor + 1:], None, memory=memory,
        retrieve=lambda _: [passage()], call=fake_model([response(), checked(True)], new_calls))
    assert followup['_memory_update'] is None and followup['retrieval']['summary_calls'] == 0
    assert new_calls[0]['request']['history']['summary'] == memory['summary']
    assert len(new_calls[0]['request']['history']['turns']) == len(history) - cursor - 1


def test_invalid_compaction_does_not_silently_drop_old_turns():
    history = [{'question': '必須保留的條件', 'answer': '內容' * 1800}]
    for value in ['', '內容' * 1500, None]:
        with pytest.raises(SourceError, match='VOICE_ANSWER_INVALID'):
            qa.answer('回到原條件', history, None, retrieve=lambda _: pytest.fail('Invalid summary reached retrieval'),
                call=lambda *args: {'summary': value})


def test_no_evidence_returns_specific_gap_without_inventing_citations():
    calls = []
    result = {'segments': [], 'queries': [], 'missing': '教材沒有提供重傳逾時的毫秒數。'}
    answer = qa.answer('等待幾毫秒？', [], None, retrieve=lambda _: [], call=fake_model([result], calls))
    assert not answer['supported'] and answer['citations'] == []
    assert '重傳逾時的毫秒數' in answer['text']


def test_verifier_can_complete_a_missing_citation_from_retrieved_original_text():
    calls=[]
    sources=[passage('heading','Value: content V'),passage('detail','V is the vector being combined with attention weights.')]
    review={'items':[{'index':0,'supported':True,'basis':'sources','reason':'','correction':'','citations':[0,1]}], 'queries':[], 'missing':''}
    result=qa.answer('V 是什麼？',[],None,retrieve=lambda _:sources,
        call=fake_model([response('V 是依注意力權重整合的內容向量。',indices=[1]),review],calls))
    assert result['supported'] and len(result['citations'])==2 and len(calls)==2
    assert len(calls[-1]['request']['sources'])==2


def test_unsupported_optional_elaboration_is_removed_without_creating_a_gap():
    calls=[]
    generated=response()
    generated['segments'].append({'text':'所有服務都使用這個設計。','citations':[0]})
    review=checked(True,False)
    review['items'][1]['reason']='多餘的普遍性推論，原問題只問FIFO。'
    review.update(queries=[],missing='')
    answer=qa.answer('佇列怎麼取資料？',[],None,retrieve=lambda _:[passage()],
        call=fake_model([generated,review],calls))
    assert answer['text']=='佇列採先進先出。' and len(calls)==2


def test_review_can_remove_unsupported_clause_without_losing_core_step():
    calls = []
    review = checked(True)
    review['items'][0].update(correction='佇列採先進先出。', reason='刪除來源未提供的固定耗時。')
    answer = qa.answer('佇列怎麼取資料？', [], None, retrieve=lambda _: [passage()],
        call=fake_model([response('佇列採先進先出，而且一定在一毫秒內完成。'), review], calls))
    assert answer['text'] == '佇列採先進先出。' and answer['supported']
    assert answer['citations'][0]['evidence'][0]['normalized_page'] == 7
    assert len(calls) == 2


def test_model_citation_markup_is_removed_without_touching_technical_literals():
    calls=[]
    generated=response('向量 [0, 1] 使用索引。\ue200cite\ue20219\ue201')
    answer=qa.answer('向量內容是什麼？',[],None,retrieve=lambda _:[passage(text='Vector [0, 1] uses indexes.')],
        call=fake_model([generated,checked(True)],calls))
    assert answer['text']=='向量 [0, 1] 使用索引。'
    assert calls[-1]['request']['segments'][0]['text']==answer['text']
    assert answer['citations'][0]['passage_id']=='p0'


def test_user_context_recall_does_not_invent_material_citations():
    calls = []
    history = [{'question': '教學代號從青松改為白樺；限制是不微調模型。', 'answer': '已了解。'}]
    text = '你先使用青松，後改為白樺；限制是不微調模型。'
    review = checked(True)
    review['items'][0].update(basis='conversation', citations=[])
    result = qa.answer('依序回顧代號與限制。', history, None, retrieve=lambda _: [],
        call=fake_model([response(text, indices=[]), review], calls))
    assert result['text'] == text and result['citations'] == []
    assert not result['supported']  # 不宣稱此對話設定由教材支持。
    assert calls[-1]['request']['history']['turns'] == history
    citation_schema = calls[-1]['schema']['properties']['items']['items']['properties']['citations']
    assert citation_schema['maxItems'] == 0 and 'enum' not in citation_schema['items']


def test_material_claim_still_requires_citations_after_conversation_recall_support():
    calls = []
    review = checked(True)
    review['items'][0]['citations'] = []
    with pytest.raises(SourceError, match='VOICE_ANSWER_INVALID'):
        qa.answer('佇列如何運作？', [], None, retrieve=lambda _: [],
            call=fake_model([response(indices=[]), review], calls))


def test_long_conversation_retrieves_original_user_conditions_within_history_budget():
    calls = []
    original = {'question': '公司文件更新只更新索引，不微調模型；回答要引用 PDF 頁碼。',
                'created_at': '2026-01-01T00:00:00+00:00'}
    rename = {'question': '公司代號改成白樺，原本限制不變。', 'created_at': '2026-01-02T00:00:00+00:00'}
    memory = {'summary': '曾討論公司文件與工具權限。'}
    review = checked(True)
    review['items'][0].update(basis='conversation', citations=[])
    result = qa.answer('最初公司文件更新的限制是什麼？', [], None, retrieve=lambda _: [],
        call=fake_model([response('不微調，並引用 PDF 頁碼。', indices=[]), review], calls),
        memory=memory, user_history=[original, rename, original])
    recalled = calls[1]['request']['history']['recalled_user_turns']
    assert recalled[0] == original and recalled.count(original) == 1
    assert calls[2]['request']['history']['recalled_user_turns'] == recalled
    assert result['retrieval']['history_tokens'] <= qa.HISTORY_TOKENS
    assert result['retrieval']['recalled_user_turns'] == len(recalled)


def test_followup_keeps_query_planning_for_context_resolution():
    calls=[];queries=[]
    history=[{'question':'Queue 是什麼？','answer':'佇列採先進先出。'}]
    result=qa.answer('那它怎麼取資料？',history,None,
        retrieve=lambda q: queries.append(q) or [passage()],
        call=fake_model([response(),checked(True)],calls))
    assert calls[0]['schema']['required']==['queries']
    assert calls[0]['request']['history']['turns']==history
    assert result['retrieval']['calls']==3 and len(queries)==1


def test_spoken_interpretation_is_shared_by_retrieval_answer_and_review():
    calls = []; searches = []
    raw = '剛才的東西高於本坦時呢？'
    used = '剛才的東西高於門檻時呢？'
    model = fake_model([response(), checked(True)], calls)
    def call(path, body):
        if 'interpreted_question' in body['schema']['properties']:
            return {'queries': ['threshold'], 'interpreted_question': used}
        assert body['request']['question'] == used
        return model(path, body)
    result = qa.answer(raw, [], None, spoken=True,
        retrieve=lambda queries: searches.append(queries) or [passage()], call=call)
    assert result['question_interpretation'] == {'original': raw, 'used': used}
    assert searches == [['threshold']]


def test_spoken_interpretation_cannot_change_explicit_numbers_or_identifiers():
    calls = []; searches = []
    raw = '模型 v2 的門檻是 0.7 嗎？'
    model = fake_model([response(), checked(True)], calls)
    def call(path, body):
        if 'interpreted_question' in body['schema']['properties']:
            return {'queries': ['v3 threshold 0.8'], 'interpreted_question': '模型 v3 的門檻是 0.8 嗎？'}
        assert body['request']['question'] == raw
        return model(path, body)
    result = qa.answer(raw, [], None, spoken=True,
        retrieve=lambda queries: searches.append(queries) or [passage()], call=call)
    assert 'question_interpretation' not in result
    assert searches == [[raw]]


def test_multiple_questions_plan_each_subject_before_retrieval():
    calls = []; searches = []
    model = fake_model([response(), checked(True)], calls)
    def call(path, body):
        if body['schema']['required'] == ['queries']:
            return {'queries': ['FIFO queue', 'LIFO stack']}
        return model(path, body)
    qa.answer('佇列如何取出？堆疊如何取出？', [], None,
        retrieve=lambda queries: searches.append(queries) or [passage()], call=call)
    assert searches[0][:2] == ['FIFO queue', 'LIFO stack']


def test_rejected_correction_is_rechecked_instead_of_silently_dropping_it():
    wrong = checked(False)
    wrong['items'][0]['correction'] = '佇列採先進先出。'
    calls = []
    result = qa.answer('佇列如何取出？', [], None, retrieve=lambda _: [passage()],
        call=fake_model([response(), wrong, checked(True)], calls))
    assert result['supported']
    assert len(calls) == 3 and 'validation_feedback' in calls[-1]['request']


def test_recalled_latest_user_change_has_room_before_old_assistant_explanations():
    memory={'summary':'較早的名稱是甲。','through_turn_id':'old','through_created_at':'2026-01-01T00:00:00+00:00'}
    history=[{'question':'回顧原始案例。','answer':''},
             {'question':'更正，目前名稱是丙。','answer':'目前是丙。'}]
    while qa._count({'summary':memory['summary'],'turns':history})<qa.HISTORY_TOKENS-150:
        history[0]['answer']+='舊助教長篇解釋。'
    original_answer=history[0]['answer']
    users=[{'question':'原始名稱甲；只能更新索引。'+'原始案例背景。'*35}, {'question':'更正，名稱改乙。'},
           {'question':'更正，目前名稱是丙。'}]
    calls=[]
    qa.answer('回顧名稱變更與目前條件。',history,None,memory=memory,user_history=users,
        retrieve=lambda _: [passage()],call=fake_model([response(),checked(True)],calls))
    packed=calls[-1]['request']['history']
    assert any(t['question']=='更正，目前名稱是丙。' for t in packed['recalled_user_turns'])
    assert any('原始名稱甲' in t['question'] for t in packed['recalled_user_turns'])
    assert qa._count(packed)<=qa.HISTORY_TOKENS
    assert history[0]['answer']==original_answer


def test_speech_terms_keep_nouns_without_copying_previous_numbers_or_conditions():
    generated=response()
    generated['speech_terms']=['擴散模型','含噪狀態','採樣器','高於門檻','0.7','v2']
    result=qa.answer('解釋生成機制。',[],None,retrieve=lambda _: [passage()],
        call=fake_model([generated,checked(True)],[]))
    assert result['speech_terms']==['擴散模型','含噪狀態','採樣器']


@pytest.mark.parametrize('raw,changed', [('風險分數不等於門檻。','風險分數等於門檻。'),('x != y','x = y')])
def test_spoken_interpretation_cannot_remove_comparison_negation(raw,changed):
    model=fake_model([response(),checked(True)],[])
    def call(path,body):
        if 'interpreted_question' in body['schema']['properties']:
            return {'interpreted_question':changed,'queries':[changed]}
        assert body['request']['question']==raw
        return model(path,body)
    result=qa.answer(raw,[],None,spoken=True,retrieve=lambda _: [passage()],call=call)
    assert 'question_interpretation' not in result

"""檢索式問答的受控回應；驗證流程，不代表真實模型品質。"""
from runtime import retrieval
from runtime.storage.tables import database_session


def grounded_answer(state, dsn, text='依照教材說明這個概念。'):
    with database_session(dsn) as db:
        rows = retrieval.search(db, state['owner'], state['material_id'], state['revision'], ['Stack LIFO'])
    assert rows
    passage = rows[0]
    state['retrieved'][passage['passage_id']] = passage
    return {'text': text, 'supported': True, 'citations': [{'passage_id': passage['passage_id'],
        'text': passage['text'], 'evidence': passage['references']}]}


def qa_model(body, text='來源支持的回答。'):
    request = body['request']
    if body['schema']['required'] == ['queries']:
        return {'queries': ['Stack LIFO']}
    if 'segments' in request:
        return {'items': [{'index': item['index'], 'supported': True, 'basis': 'sources', 'reason': '', 'correction': ''}
                          | {'citations':item['citations']} for item in request['segments']], 'queries': [], 'missing': ''}
    if not request['sources']:
        return {'segments': [], 'queries': ['Stack LIFO'], 'missing': 'Stack 原文'}
    return {'segments': [{'text': text, 'citations': [0]}], 'queries': [], 'missing': ''}

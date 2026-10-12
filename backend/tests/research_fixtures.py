"""背景研究測試的確定性模型回應；不連線、不代表真實來源品質。"""


def review_provider(path,body):
    assert path=='/semantics'
    request=body['request'];task=request['task']
    if task=='plan':
        return {'summary':'測試研究計畫','topics':['queue'],
                'searches':[{'query':'queue tutorial','topics':[0],'reason':'fixture'}]}
    if task=='find_entries':return {'entries':[]}
    if task=='screen':
        return {'items':[{'index':i['index'],'relevance':'direct','decision':'read','topics':list(range(len(request['topics']))),'reason':'fixture topic match'} for i in request['items']]}
    if task=='locate':return {'sections':[{'index':s['index'],'decision':'read','reason':'fixture: read the complete source'} for s in request['sections']]}
    if task=='read':
        return {'relevance':'direct','reason':'fixture body match',
                'supports':[{'topic':n,'passages':[0]} for n in range(len(request['topics']))]}
    if task=='follow_up':
        return {'coverage':[{'topic':n,'status':'covered' if any(i['importable'] for i in request['items']) else 'missing',
            'reason':'fixture coverage','sources':[i['index'] for i in request['items']]} for n in range(len(request['topics']))],
            'read_next':[],'searches':[]}
    raise AssertionError(task)


def search_to_selection(owner,identity,dsn):
    from runtime import research
    for _ in range(100):
        view=research.read(owner,identity,dsn=dsn)
        if view['status']!='searching':
            assert view['status']=='selecting',view['error_code']
            return view
        assert research.step(dsn=dsn)
    raise AssertionError('background research did not finish')


def continue_provider(path,body):
    result=review_provider(path,body)
    if body['request']['task']=='follow_up':
        result['searches']=[{'query':s['query'],'topics':[0],'reason':'fixture continuation'}
            for s in body['request']['searched'] if s['next_page']]
    return result

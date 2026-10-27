"""背景研究測試的確定性模型回應；不連線、不代表真實來源品質。"""


def review_provider(path,body):
    assert path=='/semantics'
    request=body['request'];task=request['task']
    if task=='plan':
        return {'summary':'測試研究計畫',
                'searches':[{'query':'queue tutorial','channel':'wikipedia','topics':[t['index'] for t in request['topics']],'reason':'fixture'}]}
    if task=='source_type':return {'type':'paper','reason':'Synthetic scholarly article fixture; type response is controlled, not a quality assessment.'}
    if task=='find_entries':return {'entries':[]}
    if task=='screen':
        return {'items':[{'index':i['index'],'relevance':'direct','decision':'read','topics':list(range(len(request['topics']))),'reason':'fixture topic match'} for i in request['items']]}
    if task=='locate':return {'sections':[{'index':s['index'],'decision':'read','reason':'fixture: read the complete source'} for s in request['sections']]}
    if task=='read':
        return {'relevance':'direct','reason':'fixture body match',
                'supports':[{'topic':n,'passages':[0]} for n in range(len(request['topics']))]}
    if task=='follow_up':
        return {'coverage':[{'topic':n,'status':'covered' if any(i['importable'] for i in request['items']) else 'missing',
            'reason':'fixture coverage','sources':[i['index'] for i in request['items']]} for n in request['coverage_topics']],
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
        result['searches']=[{'query':s['query'],'channel':'wikipedia','topics':[0],'reason':'fixture continuation'}
            for s in body['request']['searched'] if s['next_page']]
    return result


def requested_coverage(body,items):
    """模型的 coverage 回應只包含此請求指定的子題；規劃請求不重算涵蓋。"""
    return [item for item in items if item['topic'] in body['request']['coverage_topics']]


def create_confirmed_research(owner,material,request,key,*,dsn):
    """來源流程測試從已確認範圍開始；確認前不搜尋由 topics 案例獨立驗證。"""
    from runtime import topics,research
    from runtime.storage.tables import TopicScope
    from runtime.storage.database import database_session
    scope=topics.create(owner,request,key,material_id=material,dsn=dsn)
    with database_session(dsn) as db:
        row=db.get(TopicScope,scope['topic_id'])
        if row.status=='pending':
            row.proposal={'title':request,'goals':['理解原理'],'topics':['queue']}
            row.status='ready';row.version+=1
    scope=topics.read(owner,scope['topic_id'],dsn=dsn)
    # 重送已確認的請求要帶確認前看到的版本（確認時版本 +1）。
    expected=scope['version']-1 if scope['status']=='approved' else scope['version']
    approved=topics.approve(owner,scope['topic_id'],expected,scope['proposal'],key+':approve',dsn=dsn)
    return research.read(owner,approved['research_id'],dsn=dsn)

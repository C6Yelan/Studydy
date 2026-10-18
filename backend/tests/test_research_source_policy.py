"""來源類型與網址邊界；受控模型只驗證流程，不代表真實分類品質。"""
from copy import deepcopy
from hashlib import sha256
import pytest
from runtime import research_web as web, research_workflow as flow


@pytest.mark.parametrize('url,allowed',[
 ('https://en.wikipedia.org/wiki/Intelligent_agent',True),
 ('https://zh.m.wikipedia.org/wiki/人工智慧',True),
 ('https://en.wikipedia.org.evil.test/wiki/Agent',False),
 ('https://wikipedia.org/wiki/Agent',False),
 ('https://en.wikipedia.org/wiki/User%3ABlog',False),
 ('https://en.wikibooks.org/wiki/Agent',False),
 ('https://docs.python.org/3/tutorial/',False),
])
def test_only_wikipedia_articles_are_accepted(url,allowed):
    assert web.wikipedia_article(url)==allowed


def test_wikipedia_search_filters_engine_results_and_user_site_operator(monkeypatch):
    seen=[]
    def search(query,page):
        seen.append(query)
        return {'results':[{'url':url} for url in [
            'https://en.wikipedia.org/wiki/Agent', 'https://blog.test/agent',
            'https://fr.wikipedia.org/wiki/Agent', 'https://zh.wikipedia.org/wiki/Agent',
            'https://simple.wikipedia.org/wiki/Agent', 'https://zh.m.wikipedia.org/wiki/Agent',
            'https://en.m.wikipedia.org/wiki/Planning',
            'https://en.wikipedia.org.evil.test/wiki/Agent', 'https://en.wikipedia.org/wiki/Talk%3AAgent']]}
    monkeypatch.setattr(web,'_searx',search)
    rows,_,_=web.search_wikipedia(['agent site:blog.test'],1)
    assert seen==['site:en.wikipedia.org agent']
    assert [c['url'] for c in rows]==['https://en.wikipedia.org/wiki/Agent','https://en.m.wikipedia.org/wiki/Planning']
    assert rows[0]['kind']=='wikipedia'


@pytest.mark.parametrize('verdict',['other','uncertain','paper'])
def test_repository_blog_must_pass_content_type_before_reading(monkeypatch,verdict):
    text='A personal blog post, archived in a repository. References are not publication evidence.'
    candidate={'id':'source','title':'Agent research','kind':'paper','url':'https://zenodo.org/records/123',
               'eligible':True,'state':'candidate','reason':'index metadata',
               'preview':{'kind':'abstract'},'screening':{'decision':'read','reason':'relevant','basis':'abstract'}}
    digest=sha256(text.encode()).hexdigest()
    monkeypatch.setattr(flow,'inspect_candidate',lambda c:{**c,'_reading_text':text,
        'inspection_text_sha256':digest,'access':{'status':'read','url':c['url']}})
    value={'query':'Agent','mode':'self-study','search_state':{**flow.new_state(),'stage':'reading','topics':['Agent']},'candidates':[candidate]}
    calls=[]
    def provider(path,body):
        calls.append(body['request']['task'])
        assert body['request']['beginning']==text
        return {'type':verdict,'reason':'Controlled classification'}
    result=flow.advance(value,provider);c=result['candidates'][0]
    assert calls==['source_type']
    assert flow.source_allowed(c)==(verdict=='paper')
    if verdict!='paper':
        assert not c['eligible'] and c['screening']['decision']=='exclude'
        assert '_reading_text' not in c and c['review']['quotes']==[]
    else:
        assert result['search_state']['stage']=='locating'
        changed=deepcopy(c);changed['inspection_text_sha256']='new document'
        assert not flow.source_allowed(changed)


def test_unclassified_old_body_cannot_count_as_coverage_or_be_finished_as_importable():
    c={'id':'old','url':'https://zenodo.org/records/1','title':'Old indexed document','eligible':True,
       'state':'candidate','reason':'Old result','inspection_sha256':'old-bytes',
       'screening':{'decision':'read','reason':'Recheck type','basis':'source_policy'},
       'review':{'phase':'metadata','relevance':'direct','topics':[0],'quotes':[]},
       '_prior_review':{'phase':'body','relevance':'direct','topics':[0],
                        'quotes':[{'topic':0,'quote':'Previously accepted text'}],'reason':'Old judgement'}}
    data={**flow.new_state(),'stage':'following_up','topics':['Agent']}
    def provider(path,body):
        assert body['request']['items']==[]
        return {'coverage':[],'read_next':[],'searches':[]}
    result=flow.advance({'query':'Agent','mode':'self-study','search_state':data,'candidates':[c]},provider)
    assert result['search_state']['stage']=='reading'
    assert not result['search_state'].get('coverage')
    data.update(finish_reason='partial_delivery')
    flow.organize(data,[c])
    assert not c['eligible']

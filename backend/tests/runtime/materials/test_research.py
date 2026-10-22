from research_fixtures import create_confirmed_research
from copy import deepcopy
from uuid import uuid4,UUID
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions,pdf
from runtime import research,voice
from research_fixtures import review_provider,search_to_selection,continue_provider
from runtime.storage.materials import MaterialLibraryError
from runtime.source_normalization import normalize_next
from runtime.errors import SourceError
from runtime.storage.tables import MaterialResearch, MaterialSource, Material
from runtime.storage.database import database_session
from runtime.storage.knowledge_structures import read_knowledge_structure


def candidate(i='paper1'):
    return {'id':i,'kind':'paper','title':'Synthetic queue','authors':'Fixture author','year':2026,'description':'A queue removes the first inserted item first.',
        'doi':None,'url':f'https://example.org/{i}','download_url':f'https://example.org/{i}.pdf',
        'license':'cc-by','license_url':'https://creativecommons.org/licenses/by/4.0/',
        'version':'publishedVersion','eligible':True,'reason':'synthetic','state':'candidate'}


def saved_candidates(dsn,identity):
    """研究內部狀態只留在資料庫；回應不含搜尋管道、存取紀錄等除錯欄位。"""
    with database_session(dsn) as db:return deepcopy(db.get(MaterialResearch,identity).candidates)


def mock_search(monkeypatch):
    monkeypatch.setattr(research,'provider',review_provider)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
    from runtime import research_reading
    def unavailable(*a,**kw):raise SourceError('RESEARCH_DOWNLOAD_FAILED')
    monkeypatch.setattr(research_reading,'read_public_document',unavailable)
    payload=pdf('A queue removes the first inserted item first.')
    monkeypatch.setattr(research.research_sources,'acquire',lambda c,**kwargs:(payload,'application/pdf','.pdf',{'id':c['id'],'license':'cc-by'}))
    return payload


def ready(owner,material,dsn,monkeypatch):
    mock_search(monkeypatch)
    r=create_confirmed_research(owner,material,'補充佇列','research',dsn=dsn)
    identity=r['research_id'];search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'acquire',['paper1'],dsn=dsn)
    assert research.step(dsn=dsn);assert normalize_next(dsn=dsn)
    assert research.step(dsn=dsn);assert research.step(dsn=dsn)
    checked=research.read(owner,identity,dsn=dsn)
    assert checked['status']=='ready', [(c['state'],c.get('error_code')) for c in checked['candidates']]
    return identity


def test_research_full_append_preserves_voice_and_idempotence(revisions,monkeypatch):
    learner,material,settings,dsn,_,_,execute,_,old,_=revisions
    owner=learner.learner_id
    conversation=voice.create(owner,material,'voice',dsn=dsn)
    identity=ready(owner,material,dsn,monkeypatch)
    from runtime.source_normalization import read_sources
    sources = read_sources(owner, material, dsn=dsn)["sources"]
    with database_session(dsn) as db:
        row = db.get(MaterialResearch, identity)
        acquired = {c['source_id'] for c in row.candidates if c.get('source_id')}
    assert acquired
    assert {str(s['source_id']) for s in sources if s['origin'] == 'research'} == acquired
    assert any(s['origin'] == 'upload' for s in sources)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:research.submit(owner,identity,settings,dsn=dsn),range(2)))
    assert results[0]['run_id']==results[1]['run_id']
    with database_session(dsn) as db:
        from runtime.storage.tables import MaterialProcessingRun
        run=db.get(MaterialProcessingRun,results[0]['run_id'])
        assert set(run.learning_scopes)==acquired
        assert all(scope['topics']==['queue'] for scope in run.learning_scopes.values())
    outcome=execute();assert outcome.status=='succeeded',outcome.error_code
    saved=voice.read(owner,conversation['conversation_id'],dsn=dsn)
    assert saved['knowledge_structure_revision']==old['revision'] and not saved['is_current_revision']
    assert read_knowledge_structure(owner,material,revision=old['revision'],dsn=dsn).document==old
    assert research.submit(owner,identity,settings,dsn=dsn)['run_id']==outcome.run_id
    summary=research.listing(owner,material,dsn=dsn)['researches'][0]
    assert summary['run_status']=='succeeded'
    assert summary['candidate_count']==1
    assert len(summary['selection'])==1
    research.delete(owner,identity,dsn=dsn)
    assert read_knowledge_structure(owner,material,revision=old['revision'],dsn=dsn).document==old
    assert any(s['origin']=='research' for s in read_sources(owner,material,dsn=dsn)["sources"])
    with database_session(dsn) as db:
        from runtime.storage.tables import MaterialProcessingRun
        assert db.get(MaterialProcessingRun,outcome.run_id).status=='succeeded'


def test_cancel_staged_acquisition_blocks_upload_and_replay(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    r=create_confirmed_research(owner,source.material_id,'queue','r',dsn=dsn)
    identity=r['research_id'];search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'acquire',['paper1'],dsn=dsn)
    original_read=research.research_documents.read
    def acquire(*args,**kwargs):
        research.action(owner,identity,'cancel',dsn=dsn)
        return original_read(*args,**kwargs)
    monkeypatch.setattr(research.research_documents,'read',acquire)
    research.step(dsn=dsn)
    assert research.read(owner,identity,dsn=dsn)['status']=='cancelled'
    with database_session(dsn) as db:assert len(db.scalars(select(MaterialSource)).all())==1
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):research.read(uuid4(),identity,dsn=dsn)


def test_revision_conflict_keeps_acquired_sources(revisions,monkeypatch):
    learner,material,settings,dsn,add,start,execute,_,old,_=revisions;owner=learner.learner_id
    identity=ready(owner,material,dsn,monkeypatch)
    second=add('independent.pdf','A tree contains nodes and edges.')
    start([second],'independent',old['revision']);assert execute().status=='succeeded'
    with pytest.raises(SourceError,match='REVISION_CONFLICT'):research.submit(owner,identity,settings,dsn=dsn)
    r=research.read(owner,identity,dsn=dsn)
    assert r['status']=='ready' and r['candidates'][0]['state']=='ready' and not r['is_current_revision']


def pages(*results):
    """依序回傳各頁搜尋結果；自動補查用 next_page 接續時才會取到下一頁。"""
    remaining=list(results)
    return lambda *a,**kwargs:remaining.pop(0) if len(remaining)>1 else remaining[0]


def test_next_page_results_merge_and_reject_unlicensed(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    monkeypatch.setattr(research,'provider',continue_provider)
    c=candidate('unlicensed');c.update(eligible=False,license='unknown',download_url=None)
    monkeypatch.setattr(research.research_sources,'search',pages(([candidate()],'next',[]),([candidate(),c],None,[])))
    identity=create_confirmed_research(owner,source.material_id,'q','r',dsn=dsn)['research_id']
    search_to_selection(owner,identity,dsn)
    assert len(saved_candidates(dsn,identity))==2
    assert [c['id'] for c in research.read(owner,identity,dsn=dsn)['candidates']]==['paper1']
    with pytest.raises(SourceError,match='REQUEST_INVALID'):research.action(owner,identity,'acquire',['unlicensed'],dsn=dsn)


def test_research_history_is_a_summary_and_reads_share_material_lock(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','r',dsn=dsn)['research_id']
    search_to_selection(owner,identity,dsn)
    with database_session(dsn) as db:
        db.scalar(select(Material).where(Material.material_id==source.material_id).with_for_update(read=True))
        with ThreadPoolExecutor(max_workers=1) as pool:
            history=pool.submit(research.listing,owner,source.material_id,dsn=dsn).result(timeout=2)
            detail=pool.submit(research.read,owner,identity,dsn=dsn).result(timeout=2)
    assert history['researches'][0]['research_id']==identity
    assert history['researches'][0]['query']=='queue'
    assert history['researches'][0]['candidate_count']==1
    assert history['researches'][0]['run_status'] is None
    assert history['researches'][0]['created_at'] is not None
    assert 'candidates' not in history['researches'][0]
    assert detail['candidates'][0]['license']=='cc-by'
    with pytest.raises(MaterialLibraryError,match='RESOURCE_NOT_FOUND'):
        research.listing(uuid4(),source.material_id,dsn=dsn)


def test_waiting_normalization_does_not_starve_new_search(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    first=create_confirmed_research(owner,source.material_id,'first','first',dsn=dsn)['research_id']
    with database_session(dsn) as db:db.get(MaterialResearch,first).status='normalizing'
    state=research.claim(dsn=dsn)
    research.save(state,lambda row:None,dsn=dsn)
    second=create_confirmed_research(owner,source.material_id,'second','second',dsn=dsn)['research_id']
    assert research.claim(dsn=dsn)['research_id']==second


def test_analysis_retry_updates_research_entry_atomically(revisions,monkeypatch):
    from runtime import material_processing as processing
    from runtime.source_revisions import retry_revision
    from runtime.material_runtime import MaterialAnalysisError
    learner,material,settings,dsn,_,_,execute,_,_,_=revisions;owner=learner.learner_id
    identity=ready(owner,material,dsn,monkeypatch)
    submitted=research.submit(owner,identity,settings,dsn=dsn)
    actual=processing.analyze_material
    def fail(*a,**kw):raise MaterialAnalysisError('SEMANTIC_SERVICE_UNAVAILABLE')
    monkeypatch.setattr(processing,'analyze_material',fail)
    assert execute().status=='failed'
    retry=retry_revision(owner,submitted['run_id'],'retry-research',settings,dsn=dsn)
    assert research.read(owner,identity,dsn=dsn)['run_id']==retry.run_id
    assert retry_revision(owner,submitted['run_id'],'retry-research',settings,dsn=dsn).run_id==retry.run_id
    monkeypatch.setattr(processing,'analyze_material',actual)
    assert execute().status=='succeeded'
    assert research.read(owner,identity,dsn=dsn)['run']['status']=='succeeded'


def test_new_research_reuses_identical_unpublished_source(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    first=ready(owner,source.material_id,dsn,monkeypatch)
    source_id=saved_candidates(dsn,first)[0]['source_id']
    second=create_confirmed_research(owner,source.material_id,'再找同一份補充','second',dsn=dsn)['research_id']
    search_to_selection(owner,second,dsn);research.action(owner,second,'acquire',['paper1'],dsn=dsn)
    for _ in range(3):research.step(dsn=dsn)
    result=research.read(owner,second,dsn=dsn)
    assert result['status']=='ready' and saved_candidates(dsn,second)[0]['source_id']==source_id
    with database_session(dsn) as db:assert len(db.scalars(select(MaterialSource)).all())==2


def test_deleted_research_cannot_publish_late_results_or_replay_creation(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','delete-search',dsn=dsn)['research_id']
    state=research.claim(dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):
        research.delete(uuid4(),identity,dsn=dsn)
    assert research.delete(owner,identity,dsn=dsn)['status']=='deleted'
    assert research.delete(owner,identity,dsn=dsn)['status']=='deleted'
    assert not research.save(state,lambda row:setattr(row,'status','selecting'),dsn=dsn)
    assert research.claim(dsn=dsn) is None
    assert research.listing(owner,source.material_id,dsn=dsn)['researches']==[]
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):research.read(owner,identity,dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):research.action(owner,identity,'retry',dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):create_confirmed_research(owner,source.material_id,'queue','delete-search',dsn=dsn)


def test_deleted_research_keeps_source_files_and_research_origin(revisions,monkeypatch):
    learner,material,_,dsn,*_=revisions;owner=learner.learner_id
    identity=ready(owner,material,dsn,monkeypatch)
    from runtime.source_normalization import read_sources
    before=read_sources(owner,material,dsn=dsn)["sources"]
    assert any(source['origin']=='research' for source in before)
    research.delete(owner,identity,dsn=dsn)
    assert read_sources(owner,material,dsn=dsn)["sources"]==before
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        assert row.deleted_at is not None and row.lease_token is None
        assert row.candidates and row.status=='cancelled'


def test_cross_provider_next_page_deduplicates_without_replacing_candidate_or_exposing_downloads(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    first=candidate();first.update(doi='https://doi.org/10.1234/paper',discovered_by=['OpenAlex'],download_locations=[{'download_url':'https://example.org/a.pdf','license':'cc-by','version':'publishedVersion','url':'https://example.org/article','license_url':None}])
    other=deepcopy(first);other.update(id='crossref-other-id',doi='10.1234/PAPER',discovered_by=['Crossref'])
    monkeypatch.setattr(research,'provider',continue_provider)
    monkeypatch.setattr(research.research_sources,'search',pages(([first],'more',[]),([other],None,['source temporarily unavailable'])))
    work=create_confirmed_research(owner,source.material_id,'queue','cross-provider',dsn=dsn)
    search_to_selection(owner,work['research_id'],dsn)
    result=research.read(owner,work['research_id'],dsn=dsn)
    assert result['error_code']=='RESEARCH_PARTIAL_SEARCH'
    assert len(result['candidates'])==1 and len(saved_candidates(dsn,work['research_id']))==1
    saved=saved_candidates(dsn,work['research_id'])[0]
    assert saved['id']==first['id'] and saved['discovered_by']==['OpenAlex','Crossref']
    assert 'download_url' not in result['candidates'][0] and 'download_locations' not in result['candidates'][0]
    research.action(owner,work['research_id'],'acquire',[first['id']],dsn=dsn)
    assert research.read(owner,work['research_id'],dsn=dsn)['selection']==[first['id']]


def test_wikipedia_source_uses_existing_normalization_and_records_acquisition(revisions,monkeypatch):
    from runtime import research_web
    from test_research_web import BODY
    learner,material,settings,dsn,*_=revisions
    owner=learner.learner_id
    monkeypatch.setattr(research.research_sources,'public_page',lambda url,**kwargs:(BODY.encode(),url,'text/html'))
    doc=research_web.html_document(BODY.encode(),'https://en.wikipedia.org/wiki/Queue','text/html')
    item={**candidate('web-source'),'kind':'wikipedia','title':doc['title'],'authors':doc['authors'],
          'url':doc['url'],'download_url':doc['url'],'license':doc['license'],'license_url':doc['license_url'],
          'page_identity':doc['page_identity'],'version':'取得時的網頁版本'}
    monkeypatch.setattr(research,'provider',review_provider)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([item],None,[]))
    identity=create_confirmed_research(owner,material,'Wikipedia 補充','web-research',dsn=dsn)['research_id']
    search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'acquire',['web-source'],dsn=dsn)
    assert research.step(dsn=dsn)
    assert normalize_next(dsn=dsn)
    for _ in range(2):assert research.step(dsn=dsn)
    result=research.read(owner,identity,dsn=dsn)
    assert result['status']=='ready'
    acquired=result['candidates'][0]
    saved=saved_candidates(dsn,identity)[0]
    assert acquired['state']=='ready' and saved['normalization_id']
    assert saved['acquisition']['original_sha256']==doc['original_sha256']
    assert saved['acquisition']['download_url']==doc['url']
    assert saved['acquisition']['derivative_license']=='CC-BY-SA-4.0'
    assert acquired['acquisition']['acquired_at']==saved['acquisition']['acquired_at']
    assert 'download_url' not in acquired and 'page_identity' not in acquired
    assert 'quotes' not in acquired['review']
    with database_session(dsn) as db:
        assert db.get(MaterialResearch,identity).candidates[0]['review']['quotes']


def test_background_checkpoint_survives_cancel_and_retry_without_repeating_finished_search(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch);calls=[]
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:(calls.append(a) or [candidate()],None,[]))
    identity=create_confirmed_research(owner,source.material_id,'queue','resume-background',dsn=dsn)['research_id']
    assert research.read(owner,identity,dsn=dsn)['progress']['stage']=='planning'
    research.step(dsn=dsn);research.step(dsn=dsn)
    assert len(calls)==1
    checkpoint=research.read(owner,identity,dsn=dsn)['progress']
    assert checkpoint['stage']=='collecting'
    stale=research.claim(dsn=dsn)
    research.action(owner,identity,'cancel',dsn=dsn)
    assert not research.save(stale,lambda row:setattr(row,'status','selecting'),dsn=dsn)
    # 停止後進度保持原檢查點，只不再送出進行中的說明。
    assert research.read(owner,identity,dsn=dsn)['progress']=={**checkpoint,'summary':''}
    research.action(owner,identity,'retry',dsn=dsn)
    done=search_to_selection(owner,identity,dsn)
    saved=saved_candidates(dsn,identity)
    assert len(calls)==1 and done['progress']['stage']=='complete'
    assert sum(c['access']['status']=='read' and c['review']['phase']=='body' for c in saved)==1
    assert '_reading_text' not in done['candidates'][0] and '_review_chunks' not in done['candidates'][0]


def test_cancel_while_reading_does_not_publish_late_body(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','cancel-reading',dsn=dsn)['research_id']
    for _ in range(10):
        if research.read(owner,identity,dsn=dsn)['progress']['stage']=='reading':break
        assert research.step(dsn=dsn)
    assert research.read(owner,identity,dsn=dsn)['progress']['stage']=='reading'
    def inspect(item):
        research.action(owner,identity,'cancel',dsn=dsn)
        return {**item,'access':{'status':'read'},'_reading_text':'late content'}
    monkeypatch.setattr(research.research_workflow,'inspect_candidate',inspect)
    research.step(dsn=dsn)
    view=research.read(owner,identity,dsn=dsn)
    assert view['status']=='cancelled' and 'access' not in saved_candidates(dsn,identity)[0]


def test_background_source_text_is_lossless_and_summary_does_not_expose_it(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','nul-reading',dsn=dsn)['research_id']
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        row.candidates=[{**candidate(),'_reading_text':'literal\x00source','_read_offset':0,'access':{'status':'read','characters':14}}]
    with database_session(dsn) as db:assert db.get(MaterialResearch,identity).candidates[0]['_reading_text']=='literal\x00source'
    assert research.listing(owner,source.material_id,dsn=dsn)['researches'][0]['candidate_count']==1
    assert 'literal' not in repr(research.read(owner,identity,dsn=dsn))


def test_plan_uses_current_material_concepts_without_sending_evidence_or_answers(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch);payloads=[]
    def provider(path,body):payloads.append(body['request']);return review_provider(path,body)
    monkeypatch.setattr(research,'provider',provider)
    create_confirmed_research(owner,source.material_id,'補充 Stack 原理','existing-context',dsn=dsn)
    research.step(dsn=dsn)
    with database_session(dsn) as db:revision=db.get(Material,source.material_id).head_revision
    stored=read_knowledge_structure(owner,source.material_id,revision=revision,dsn=dsn).document
    assert all(c['label'] in payloads[0]['existing_concepts'] for c in stored['concepts'])
    assert 'evidence' not in payloads[0] and 'answers' not in payloads[0]


def test_research_snapshot_survives_restart_and_upstream_changes(closed_loop,monkeypatch):
    from runtime.storage.tables import ResearchDocument
    from runtime.storage.database import dispose_database_engine
    from runtime.storage.artifacts import open_verified_artifact
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    original=mock_search(monkeypatch)
    work=create_confirmed_research(owner,source.material_id,'queue','snapshot-restart',dsn=dsn)
    identity=work['research_id'];view=search_to_selection(owner,identity,dsn)
    assert '_document_snapshot' not in view['candidates'][0] and '_acquired_document' not in view['candidates'][0]
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity);receipt=row.candidates[0]['_document_snapshot']
        assert db.get(ResearchDocument,(identity,bytes.fromhex(receipt['sha256']))) is not None
    research.research_sources._PUBLIC_CACHE.clear();dispose_database_engine(dsn)
    def changed(*args,**kwargs):raise AssertionError('A verified snapshot must not be downloaded again')
    monkeypatch.setattr(research.research_sources,'acquire',changed)
    research.action(owner,identity,'acquire',['paper1'],dsn=dsn)
    assert research.step(dsn=dsn)
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity);candidate=row.candidates[0]
        assert candidate['state']=='normalizing'
        artifact=db.get(MaterialSource,UUID(candidate['source_id'])).original_artifact_id
    with open_verified_artifact(owner,artifact,dsn=dsn) as stored:
        assert stored.file.read()==original
    research.delete(owner,identity,dsn=dsn)
    with database_session(dsn) as db:assert db.get(ResearchDocument,(identity,bytes.fromhex(receipt['sha256']))) is None
    with open_verified_artifact(owner,artifact,dsn=dsn) as stored:
        assert stored.file.read()==original


def test_snapshot_scope_and_integrity_are_checked_before_use(closed_loop,monkeypatch):
    from runtime.storage.tables import ResearchDocument
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    original=mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','snapshot-integrity',dsn=dsn)['research_id']
    search_to_selection(owner,identity,dsn)
    with database_session(dsn) as db:receipt=deepcopy(db.get(MaterialResearch,identity).candidates[0]['_document_snapshot'])
    with pytest.raises(SourceError,match='RESEARCH_SNAPSHOT_UNAVAILABLE'):
        research.research_documents.read(uuid4(),receipt,receipt['sha256'],dsn=dsn)
    with pytest.raises(SourceError,match='RESEARCH_SNAPSHOT_UNAVAILABLE'):
        research.research_documents.read(identity,receipt,'0'*64,dsn=dsn)
    with database_session(dsn) as db:
        row=db.get(ResearchDocument,(identity,bytes.fromhex(receipt['sha256'])))
        row.content=original[:-1]+bytes([original[-1]^1])
    with pytest.raises(SourceError,match='RESEARCH_SNAPSHOT_UNAVAILABLE'):
        research.research_documents.read(identity,receipt,receipt['sha256'],dsn=dsn)


def test_html_paper_snapshot_survives_restart_and_normalizes_selected_passages(closed_loop,monkeypatch):
    from hashlib import sha256
    from runtime.storage.tables import ResearchDocument, SourceNormalization
    from runtime.storage.database import dispose_database_engine
    from runtime.storage.artifacts import open_verified_artifact
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    prefix='Synthetic queue\nSource: https://example.org/paper1\n\n'
    payload=(prefix+'A queue removes the first inserted item first.').encode()
    metadata={'id':'paper1','license':'cc-by','format':'html','access_method':'html','body_offset':len(prefix),
        'original_sha256':sha256(b'<html>The original source page</html>').hexdigest()}
    monkeypatch.setattr(research.research_sources,'acquire',lambda *a,**k:(payload,'text/plain','.txt',metadata))
    identity=create_confirmed_research(owner,source.material_id,'queue','html-snapshot',dsn=dsn)['research_id']
    view=search_to_selection(owner,identity,dsn)
    assert view['candidates'][0]['eligible']
    with database_session(dsn) as db:receipt=db.get(MaterialResearch,identity).candidates[0]['_document_snapshot']
    dispose_database_engine(dsn)
    data,media,suffix,saved=research.research_documents.read(identity,receipt,receipt['sha256'],dsn=dsn)
    assert data==payload and media=='text/plain' and suffix=='.txt'
    assert saved['original_sha256']==metadata['original_sha256']!=receipt['sha256']
    def forbidden(*a,**k):raise AssertionError('Do not fetch a different HTML page after review')
    monkeypatch.setattr(research.research_sources,'acquire',forbidden)
    research.action(owner,identity,'acquire',['paper1'],dsn=dsn);assert research.step(dsn=dsn)
    assert normalize_next(dsn=dsn)
    with database_session(dsn) as db:
        item=db.get(MaterialResearch,identity).candidates[0]
        original=db.get(MaterialSource,UUID(item['source_id']))
        job=db.scalar(select(SourceNormalization).where(SourceNormalization.source_id==original.source_id))
        assert job.status=='ready' and job.analysis_scope['pages']
        artifact=original.original_artifact_id
    with open_verified_artifact(owner,artifact,dsn=dsn) as stored:assert stored.file.read()==payload


def test_cancel_before_reading_checkpoint_does_not_save_a_snapshot(closed_loop,monkeypatch):
    from runtime.storage.tables import ResearchDocument
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    payload=mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','snapshot-cancel',dsn=dsn)['research_id']
    for _ in range(10):
        if research.read(owner,identity,dsn=dsn)['progress']['stage']=='reading':break
        research.step(dsn=dsn)
    def cancelled(candidate,**kwargs):
        research.action(owner,identity,'cancel',dsn=dsn)
        return payload,'application/pdf','.pdf',{'id':candidate['id'],'license':'cc-by'}
    monkeypatch.setattr(research.research_sources,'acquire',cancelled)
    research.step(dsn=dsn)
    assert research.read(owner,identity,dsn=dsn)['status']=='cancelled'
    with database_session(dsn) as db:assert db.scalar(select(ResearchDocument).where(ResearchDocument.research_id==identity)) is None


def test_partial_acquisition_submits_only_ready_sources_and_keeps_failures(revisions,monkeypatch):
    learner,material,settings,dsn,_,_,execute,_,old,_=revisions
    owner=learner.learner_id
    identity=ready(owner,material,dsn,monkeypatch)
    new_text='A queue accepts items at its tail.'
    new_data=pdf(new_text)
    from hashlib import sha256
    from runtime.storage.tables import ResearchDocument
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        candidates=[*row.candidates,{**candidate('failed'),'state':'failed','error_code':'RESEARCH_DOWNLOAD_FAILED',
            'inspection_sha256':sha256(new_data).hexdigest(),
            'source_type_review':{'type':'paper','reason':'Controlled reviewed-paper fixture','document_key':sha256(new_data).hexdigest()},
            '_acquired_document':(new_data,{'id':'failed','license':'cc-by'}),
            'review':{'phase':'body','relevance':'direct','topics':[0],'reason':'Useful new operation','quotes':[{'topic':0,'quote':new_text}]}}]
        research.research_documents.persist(db,identity,candidates)
        row.candidates=candidates
        row.selection=[*row.selection,'failed'];row.status='normalizing'
    assert research.step(dsn=dsn)
    prepared=research.read(owner,identity,dsn=dsn)
    assert prepared['status']=='ready' and prepared['error_code']=='RESEARCH_ITEMS_FAILED'
    submitted=research.submit(owner,identity,settings,dsn=dsn)
    assert submitted['selection']==['paper1','failed']
    outcome=execute()
    assert outcome.status=='succeeded'
    assert research.read(owner,identity,dsn=dsn)['candidates'][1]['state']=='failed'
    document=read_knowledge_structure(owner,material,run_id=outcome.run_id,dsn=dsn).document
    assert len(document['input_binding']['manifest']['items'])==len(old['input_binding']['manifest']['items'])+1

    # 已發布部分成果仍可重試原選取的失敗來源，不重複追加第一份，也不重新下載快照。
    with database_session(dsn) as db:
        assert db.get(ResearchDocument,(identity,sha256(new_data).digest())) is not None
    assert research.action(owner,identity,'retry',dsn=dsn)['status']=='acquiring'
    assert research.step(dsn=dsn)
    assert normalize_next(dsn=dsn)
    assert research.step(dsn=dsn)
    assert research.step(dsn=dsn)
    resumed=research.submit(owner,identity,settings,dsn=dsn)
    assert resumed['run_id']!=outcome.run_id
    assert resumed['selection']==['paper1','failed']
    with database_session(dsn) as db:
        assert db.get(ResearchDocument,(identity,sha256(new_data).digest())) is None
    assert read_knowledge_structure(owner,material,run_id=outcome.run_id,dsn=dsn).document==document


def test_view_sends_only_learner_facing_fields(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=create_confirmed_research(owner,source.material_id,'queue','slim-view',dsn=dsn)['research_id']
    internal={'discovered_by':['OpenAlex'],'screening':{'decision':'read','reason':'r','basis':'summary'},'preview':{'text':'p'},
        'access':{'status':'read','method':'pdf','attempts':[{'url':'https://example.org/a','outcome':'read','http_status':200}]},
        'research_topics':[0],'description_kind':'abstract','inspection_sha256':'digest','source_id':str(uuid4())}
    usable={**candidate('usable'),**internal,'source_type_review':{'type':'paper','reason':'r','document_key':'digest'},
        'review':{'phase':'body','relevance':'direct','reason':'切題','topics':[0],'scope':'sections','quotes':[{'quote':'q'}]},
        'acquisition':{'license_text':'CC BY','acquired_at':'2026-10-01T00:00:00Z','original_sha256':'x','download_url':'https://example.org/a.pdf'}}
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        row.status='selecting'
        row.search_state={**row.search_state,'stage':'complete','round':2,'summary':'舊說明','warnings':['RESEARCH_NO_NEW_EVIDENCE'],'finish_reason':'covered',
            'coverage':[{'topic':'佇列：順序','status':'missing','reason':'r','source_ids':['usable']}]}
        row.candidates=[usable,{**candidate('unread'),**internal},{**usable,'id':'copy','duplicate_of':'usable'}]
    view=research.read(owner,identity,dsn=dsn)
    assert [c['id'] for c in view['candidates']]==['usable']
    assert set(view['candidates'][0])=={'id','kind','title','authors','year','url','license','license_url','eligible','state','acquisition','review'}
    assert set(view['candidates'][0]['acquisition'])=={'license_text','acquired_at'}
    assert set(view['candidates'][0]['review'])=={'phase','relevance','reason','scope'}
    assert view['progress']=={'stage':'complete','completed':None,'total':None,'unit':'queries','topics':[{'topic':'佇列：順序','status':'missing'}],
        'summary':'','finish_reason':'covered','waiting_until':None}
    forbidden={'screening','preview','access','discovered_by','version','research_topics','description_kind','source_type_review','source_id',
        'doi','duplicate_of','round','warnings','unavailable_sources','candidate_count','read_count','importable_count','screened_count',
        'mode','cursor','search_query','base_revision','topic_id'}
    def keys(value):
        if isinstance(value,dict):
            for k,v in value.items():yield k;yield from keys(v)
        elif isinstance(value,list):
            for v in value:yield from keys(v)
    # 已核准範圍是使用者自己的規劃，其 topics 鍵不屬於內部欄位。
    assert not forbidden & set(keys({k:v for k,v in view.items() if k!='approved_scope'}))

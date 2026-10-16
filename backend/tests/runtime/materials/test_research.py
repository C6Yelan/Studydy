from copy import deepcopy
from uuid import uuid4,UUID
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions,pdf
from runtime import research,voice
from research_fixtures import review_provider,search_to_selection,continue_provider
from runtime.card_sets import CardSetError
from runtime.source_normalization import SourceError,normalize_next
from runtime.storage.tables import MaterialResearch,MaterialSource,Material,database_session
from runtime.storage.knowledge_structures import read_knowledge_structure


def candidate(i='paper1'):
    return {'id':i,'kind':'paper','title':'Synthetic queue','authors':'Fixture author','year':2026,'description':'A queue removes the first inserted item first.',
        'doi':None,'url':f'https://example.org/{i}','download_url':f'https://example.org/{i}.pdf',
        'license':'cc-by','license_url':'https://creativecommons.org/licenses/by/4.0/',
        'version':'publishedVersion','eligible':True,'reason':'synthetic','state':'candidate'}


def mock_search(monkeypatch):
    monkeypatch.setattr(research,'provider',review_provider)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([candidate()],None,[]))
    from runtime import research_reading
    def unavailable(*a,**kw):raise SourceError('RESEARCH_DOWNLOAD_FAILED')
    monkeypatch.setattr(research_reading,'read_public_document',unavailable)
    payload=pdf('A queue removes the first inserted item first.')
    monkeypatch.setattr(research.research_sources,'acquire',lambda c,**kwargs:(payload,'application/pdf','.pdf',{'id':c['id'],'license':'cc-by'}))


def ready(owner,material,dsn,monkeypatch):
    mock_search(monkeypatch)
    r=research.create(owner,material,'補充佇列','review','research',dsn=dsn)
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
    sources = read_sources(owner, material, dsn=dsn)
    with database_session(dsn) as db:
        row = db.get(MaterialResearch, identity)
        acquired = {c['source_id'] for c in row.candidates if c.get('source_id')}
    assert acquired
    assert {str(s['source_id']) for s in sources if s['origin'] == 'research'} == acquired
    assert any(s['origin'] == 'upload' for s in sources)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:research.submit(owner,identity,settings,dsn=dsn),range(2)))
    assert results[0]['run_id']==results[1]['run_id']
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
    assert any(s['origin']=='research' for s in read_sources(owner,material,dsn=dsn))
    with database_session(dsn) as db:
        from runtime.storage.tables import MaterialProcessingRun
        assert db.get(MaterialProcessingRun,outcome.run_id).status=='succeeded'


def test_cancel_staged_acquisition_blocks_upload_and_replay(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    r=research.create(owner,source.material_id,'queue','self-study','r',dsn=dsn)
    identity=r['research_id'];search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'acquire',['paper1'],dsn=dsn)
    def acquire(c):
        research.action(owner,identity,'cancel',dsn=dsn)
        return pdf('A queue removes elements in FIFO order.'),'application/pdf','.pdf',{'id':c['id']}
    monkeypatch.setattr(research.research_sources,'acquire',acquire)
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


def test_more_results_preserve_selection_and_reject_unlicensed(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([candidate()], 'next',[]))
    identity=research.create(owner,source.material_id,'q','review','r',dsn=dsn)['research_id']
    search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'more',dsn=dsn)
    monkeypatch.setattr(research,'provider',continue_provider)
    c=candidate('unlicensed');c.update(eligible=False,license='unknown',download_url=None)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([candidate(),c], None,[]))
    search_to_selection(owner,identity,dsn)
    assert len(research.read(owner,identity,dsn=dsn)['candidates'])==2
    with pytest.raises(SourceError,match='REQUEST_INVALID'):research.action(owner,identity,'acquire',['unlicensed'],dsn=dsn)


def test_research_history_is_a_summary_and_reads_share_material_lock(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=research.create(owner,source.material_id,'queue','review','r',dsn=dsn)['research_id']
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
    with pytest.raises(CardSetError,match='RESOURCE_NOT_FOUND'):
        research.listing(uuid4(),source.material_id,dsn=dsn)


def test_waiting_normalization_does_not_starve_new_search(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    first=research.create(owner,source.material_id,'first','review','first',dsn=dsn)['research_id']
    with database_session(dsn) as db:db.get(MaterialResearch,first).status='normalizing'
    state=research.claim(dsn=dsn)
    research.save(state,lambda row:None,dsn=dsn)
    second=research.create(owner,source.material_id,'second','self-study','second',dsn=dsn)['research_id']
    assert research.claim(dsn=dsn)['research_id']==second


def test_analysis_retry_updates_research_entry_atomically(revisions,monkeypatch):
    from runtime import material_processing as processing
    from runtime.source_revisions import retry_revision
    from pdf_evidence.material_pipeline import MaterialAnalysisError
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
    source_id=research.read(owner,first,dsn=dsn)['candidates'][0]['source_id']
    second=research.create(owner,source.material_id,'再找同一份補充','self-study','second',dsn=dsn)['research_id']
    search_to_selection(owner,second,dsn);research.action(owner,second,'acquire',['paper1'],dsn=dsn)
    for _ in range(3):research.step(dsn=dsn)
    result=research.read(owner,second,dsn=dsn)
    assert result['status']=='ready' and result['candidates'][0]['source_id']==source_id
    with database_session(dsn) as db:assert len(db.scalars(select(MaterialSource)).all())==2


def test_deleted_research_cannot_publish_late_results_or_replay_creation(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=research.create(owner,source.material_id,'queue','review','delete-search',dsn=dsn)['research_id']
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
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):research.create(owner,source.material_id,'queue','review','delete-search',dsn=dsn)


def test_deleted_research_keeps_source_files_and_research_origin(revisions,monkeypatch):
    learner,material,_,dsn,*_=revisions;owner=learner.learner_id
    identity=ready(owner,material,dsn,monkeypatch)
    from runtime.source_normalization import read_sources
    before=read_sources(owner,material,dsn=dsn)
    assert any(source['origin']=='research' for source in before)
    research.delete(owner,identity,dsn=dsn)
    assert read_sources(owner,material,dsn=dsn)==before
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        assert row.deleted_at is not None and row.lease_token is None
        assert row.candidates and row.status=='cancelled'


def test_cross_provider_more_deduplicates_without_replacing_candidate_or_exposing_downloads(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    first=candidate();first.update(doi='https://doi.org/10.1234/paper',discovered_by=['OpenAlex'],download_locations=[{'download_url':'https://example.org/a.pdf','license':'cc-by','version':'publishedVersion','url':'https://example.org/article','license_url':None}])
    monkeypatch.setattr(research,'provider',review_provider)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([first],'more',[]))
    work=research.create(owner,source.material_id,'queue','review','cross-provider',dsn=dsn)
    search_to_selection(owner,work['research_id'],dsn)
    other=deepcopy(first);other.update(id='crossref-other-id',doi='10.1234/PAPER',discovered_by=['Crossref'])
    research.action(owner,work['research_id'],'more',dsn=dsn)
    monkeypatch.setattr(research,'provider',continue_provider)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([other],None,['source temporarily unavailable']))
    search_to_selection(owner,work['research_id'],dsn)
    result=research.read(owner,work['research_id'],dsn=dsn)
    assert result['error_code']=='RESEARCH_PARTIAL_SEARCH'
    assert len(result['candidates'])==1
    saved=result['candidates'][0]
    assert saved['id']==first['id'] and saved['discovered_by']==['OpenAlex','Crossref']
    assert 'download_url' not in saved and 'download_locations' not in saved
    research.action(owner,work['research_id'],'acquire',[first['id']],dsn=dsn)
    assert research.read(owner,work['research_id'],dsn=dsn)['selection']==[first['id']]


def test_web_source_uses_existing_normalization_and_records_acquisition(revisions,monkeypatch):
    from runtime import research_web
    from test_research_web import BODY
    learner,material,settings,dsn,*_=revisions
    owner=learner.learner_id
    monkeypatch.setattr(research.research_sources,'public_page',lambda url,**kwargs:(BODY.encode(),url,'text/html'))
    doc=research_web._web_document('https://example.org/lesson')
    item={**candidate('web-source'),'kind':'web','title':doc['title'],'authors':doc['authors'],
          'url':doc['url'],'download_url':doc['url'],'license':doc['license'],'license_url':doc['license_url'],
          'page_identity':doc['page_identity'],'version':'取得時的網頁版本'}
    monkeypatch.setattr(research,'provider',review_provider)
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:([item],None,[]))
    identity=research.create(owner,material,'網頁補充','review','web-research',dsn=dsn)['research_id']
    search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'acquire',['web-source'],dsn=dsn)
    assert research.step(dsn=dsn)
    assert normalize_next(dsn=dsn)
    for _ in range(2):assert research.step(dsn=dsn)
    result=research.read(owner,identity,dsn=dsn)
    assert result['status']=='ready'
    acquired=result['candidates'][0]
    assert acquired['state']=='ready' and acquired['normalization_id']
    assert acquired['acquisition']['original_sha256']==doc['original_sha256']
    assert acquired['acquisition']['download_url']==doc['url']
    assert acquired['acquisition']['derivative_license']=='CC-BY-SA-4.0'
    assert 'download_url' not in acquired and 'page_identity' not in acquired
    assert 'quotes' not in acquired['review']
    with database_session(dsn) as db:
        assert db.get(MaterialResearch,identity).candidates[0]['review']['quotes']


def test_background_checkpoint_survives_cancel_and_retry_without_repeating_finished_search(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch);calls=[]
    monkeypatch.setattr(research.research_sources,'search',lambda *a,**kwargs:(calls.append(a) or [candidate()],None,[]))
    identity=research.create(owner,source.material_id,'queue','review','resume-background',dsn=dsn)['research_id']
    assert research.read(owner,identity,dsn=dsn)['progress']['stage']=='planning'
    research.step(dsn=dsn);research.step(dsn=dsn)
    assert len(calls)==1
    checkpoint=research.read(owner,identity,dsn=dsn)['progress']
    assert checkpoint['stage']=='collecting'
    stale=research.claim(dsn=dsn)
    research.action(owner,identity,'cancel',dsn=dsn)
    assert not research.save(stale,lambda row:setattr(row,'status','selecting'),dsn=dsn)
    assert research.read(owner,identity,dsn=dsn)['progress']==checkpoint
    research.action(owner,identity,'retry',dsn=dsn)
    done=search_to_selection(owner,identity,dsn)
    assert len(calls)==1 and done['progress']['stage']=='complete' and done['progress']['read_count']==1
    assert '_reading_text' not in done['candidates'][0] and '_review_chunks' not in done['candidates'][0]


def test_cancel_while_reading_does_not_publish_late_body(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=research.create(owner,source.material_id,'queue','review','cancel-reading',dsn=dsn)['research_id']
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
    assert view['status']=='cancelled' and 'access' not in view['candidates'][0]


def test_background_source_text_is_lossless_and_summary_does_not_expose_it(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=research.create(owner,source.material_id,'queue','review','nul-reading',dsn=dsn)['research_id']
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        row.candidates=[{**candidate(),'_reading_text':'literal\x00source','_read_offset':0,'access':{'status':'read','characters':14}}]
    with database_session(dsn) as db:assert db.get(MaterialResearch,identity).candidates[0]['_reading_text']=='literal\x00source'
    assert research.listing(owner,source.material_id,dsn=dsn)['researches'][0]['candidate_count']==1
    assert '_reading_text' not in research.read(owner,identity,dsn=dsn)['candidates'][0]


def test_plan_uses_current_material_concepts_without_sending_evidence_or_answers(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch);payloads=[]
    def provider(path,body):payloads.append(body['request']);return review_provider(path,body)
    monkeypatch.setattr(research,'provider',provider)
    research.create(owner,source.material_id,'補充 Stack 原理','review','existing-context',dsn=dsn)
    research.step(dsn=dsn)
    with database_session(dsn) as db:revision=db.get(Material,source.material_id).head_revision
    stored=read_knowledge_structure(owner,source.material_id,revision=revision,dsn=dsn).document
    assert all(c['label'] in payloads[0]['existing_concepts'] for c in stored['concepts'])
    assert 'evidence' not in payloads[0] and 'answers' not in payloads[0]


def test_summary_migration_preserves_cancelled_work_and_lossless_source_text(closed_loop,monkeypatch):
    from pathlib import Path
    from sqlalchemy import text
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=research.create(owner,source.material_id,'queue','review','summary-migration',dsn=dsn)['research_id']
    finished={**candidate('finished'),'review':{'phase':'body','relevance':'direct','reason':'saved body','topics':[0],'quotes':[{'topic':0,'quote':'FIFO'}]}}
    partial={**candidate('partial'),'_reading_text':'literal\x00source','_read_offset':3,'_review_chunks':[{'reason':'saved partial','quotes':[]}]}
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity);row.status='cancelled'
        row.search_state={'stage':'assessing','round':1,'topics':['queue'],'searches':[]}
        row.candidates=[finished,partial,candidate('pending')]
    migration=(Path(__file__).resolve().parents[3]/'migrations/0021_research_summary_screening.sql').read_text()
    with database_session(dsn) as db:db.execute(text(migration))
    with database_session(dsn) as db:
        row=db.get(MaterialResearch,identity)
        assert row.status=='cancelled' and row.search_state['stage']=='collecting'
        assert row.candidates[0]['review']==finished['review']
        assert row.candidates[1]['_reading_text']=='literal\x00source' and row.candidates[1]['_read_offset']==3
        assert [c.get('screening',{}).get('decision') for c in row.candidates]==['read','read',None]


def test_finish_keeps_verified_sources_and_rejects_late_worker(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    identity=research.create(owner,source.material_id,'queue','review','finish-sources',dsn=dsn)['research_id']
    with pytest.raises(SourceError,match='SOURCE_NOT_READY'):
        research.action(owner,identity,'finish',dsn=dsn)
    search_to_selection(owner,identity,dsn)
    research.action(owner,identity,'more',dsn=dsn)
    stale=research.claim(dsn=dsn)
    with pytest.raises(SourceError,match='RESOURCE_NOT_FOUND'):
        research.action(uuid4(),identity,'finish',dsn=dsn)
    result=research.action(owner,identity,'finish',dsn=dsn)
    assert result['status']=='selecting' and result['progress']['finish_reason']=='user_stopped'
    assert result['candidates'][0]['eligible']
    assert not research.save(stale,lambda row:setattr(row,'status','searching'),dsn=dsn)
    research.action(owner,identity,'cancel',dsn=dsn)
    restored=research.action(owner,identity,'finish',dsn=dsn)
    assert restored['status']=='selecting'
    assert research.action(owner,identity,'acquire',['paper1'],dsn=dsn)['status']=='acquiring'

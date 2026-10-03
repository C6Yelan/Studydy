from copy import deepcopy
from uuid import uuid4,UUID
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from product_fixtures import closed_loop
from sources.test_source_revisions import revisions,pdf
from runtime import research,voice
from runtime.source_normalization import SourceError,normalize_next
from runtime.storage.tables import MaterialResearch,MaterialSource,Material,database_session
from runtime.storage.knowledge_structures import read_knowledge_structure


def candidate(i='paper1'):
    return {'id':i,'kind':'paper','title':'Synthetic queue','authors':'Fixture author','year':2026,
        'doi':None,'url':'https://example.org/paper','download_url':'https://example.org/paper.pdf',
        'license':'cc-by','license_url':'https://creativecommons.org/licenses/by/4.0/',
        'version':'publishedVersion','eligible':True,'reason':'synthetic','state':'candidate'}


def mock_search(monkeypatch):
    monkeypatch.setattr(research,'provider',lambda *a:{'query':'queue'})
    monkeypatch.setattr(research.research_sources,'search',lambda *a:([candidate()],None,[]))
    payload=pdf('A queue removes the first inserted item first.')
    monkeypatch.setattr(research.research_sources,'acquire',lambda c:(payload,'application/pdf','.pdf',{'id':c['id'],'license':'cc-by'}))


def ready(owner,material,dsn,monkeypatch):
    mock_search(monkeypatch)
    r=research.create(owner,material,'補充佇列','review','research',dsn=dsn)
    identity=r['research_id'];assert research.step(dsn=dsn)
    research.action(owner,identity,'acquire',['paper1'],dsn=dsn)
    assert research.step(dsn=dsn);assert normalize_next(dsn=dsn)
    assert research.step(dsn=dsn);assert research.step(dsn=dsn)
    assert research.read(owner,identity,dsn=dsn)['status']=='ready'
    return identity


def test_research_full_append_preserves_voice_and_idempotence(revisions,monkeypatch):
    learner,material,settings,dsn,_,_,execute,_,old,_=revisions
    owner=learner.learner_id
    conversation=voice.create(owner,material,'voice',dsn=dsn)
    identity=ready(owner,material,dsn,monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:research.submit(owner,identity,settings,dsn=dsn),range(2)))
    assert results[0]['run_id']==results[1]['run_id']
    outcome=execute();assert outcome.status=='succeeded',outcome.error_code
    saved=voice.read(owner,conversation['conversation_id'],dsn=dsn)
    assert saved['knowledge_structure_revision']==old['revision'] and not saved['is_current_revision']
    assert read_knowledge_structure(owner,material,revision=old['revision'],dsn=dsn).document==old
    assert research.submit(owner,identity,settings,dsn=dsn)['run_id']==outcome.run_id


def test_cancel_staged_acquisition_blocks_upload_and_replay(closed_loop,monkeypatch):
    learner,source,_,_,dsn,_=closed_loop;owner=learner.learner_id
    mock_search(monkeypatch)
    r=research.create(owner,source.material_id,'queue','self-study','r',dsn=dsn)
    identity=r['research_id'];research.step(dsn=dsn)
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
    monkeypatch.setattr(research.research_sources,'search',lambda *a:([candidate()], 'next',[]))
    identity=research.create(owner,source.material_id,'q','review','r',dsn=dsn)['research_id']
    research.step(dsn=dsn)
    research.action(owner,identity,'more',dsn=dsn)
    c=candidate('unlicensed');c['eligible']=False
    monkeypatch.setattr(research.research_sources,'search',lambda *a:([candidate(),c], None,[]))
    research.step(dsn=dsn)
    assert len(research.read(owner,identity,dsn=dsn)['candidates'])==2
    with pytest.raises(SourceError,match='REQUEST_INVALID'):research.action(owner,identity,'acquire',['unlicensed'],dsn=dsn)


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
    research.step(dsn=dsn);research.action(owner,second,'acquire',['paper1'],dsn=dsn)
    for _ in range(3):research.step(dsn=dsn)
    result=research.read(owner,second,dsn=dsn)
    assert result['status']=='ready' and result['candidates'][0]['source_id']==source_id
    with database_session(dsn) as db:assert len(db.scalars(select(MaterialSource)).all())==2

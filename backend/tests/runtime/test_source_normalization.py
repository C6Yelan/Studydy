"""B2-I 使用 disposable DB／合成來源，不使用產品模型或資料。"""
from copy import deepcopy
import io
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import select
from document_normalization.converter import convert,conversion_policy,MIME
from runtime.source_normalization import create_draft,upload_source,read_sources,normalize_next,SourceError
from runtime.source_revisions import create_revision
from runtime.source_resolver import bind_structure_input,resolve_evidence_source
from runtime.storage.knowledge_structures import publish_knowledge_structure,read_knowledge_structure
from runtime.storage.source_artifacts import open_verified_artifact
from runtime.storage.tables import MaterialSourceSet,SourceNormalization,Artifact,database_session
from runtime.material_processing import claim_next_material_processing_run,_record_progress,create_material_processing_run,MaterialProcessingError
from runtime.material_discard import request_material_discard
from test_closed_loop_v1 import closed_loop,_structure

ROOT=Path(__file__).resolve().parents[3]
TEXT=b'Stacks\nA stack follows LIFO order.\nPush adds an item to the top of the stack.\nPop removes the most recently added item.\n'

@pytest.fixture
def normalizer(monkeypatch):
    executable=ROOT/'.studydy-runtime/normalizer-venv/bin/python'
    if not executable.exists():pytest.skip('Dedicated normalizer environment required; see docs/document-normalization.md')
    monkeypatch.setenv('STUDYDY_NORMALIZER_PYTHON',str(executable))
    return executable


def test_source_round_trip_freeze_resume_and_owned_delete(closed_loop,normalizer,tmp_path,monkeypatch):
    learner,old_source,settings,old_structure,dsn,_=closed_loop
    owner=learner.learner_id
    material=create_draft(owner,'notes.txt','draft',dsn=dsn)
    assert create_draft(owner,'notes.txt','draft',dsn=dsn)==material
    identity=upload_source(owner,material,TEXT,'notes.txt','text/plain','upload-source',dsn=dsn)
    assert upload_source(owner,material,TEXT,'notes.txt','text/plain','upload-source',dsn=dsn)==identity
    job=read_sources(owner,material,dsn=dsn)[0]
    with pytest.raises(SourceError,match='SOURCE_NOT_READY'):create_revision(owner,material,[job['normalization_id']],'run',settings,dsn=dsn)
    with pytest.raises(SourceError,match='IDEMPOTENCY_CONFLICT'):upload_source(owner,material,TEXT+b'changed','notes.txt','text/plain','upload-source',dsn=dsn)
    with pytest.raises(SourceError,match='DUPLICATE_SOURCE'):upload_source(owner,material,TEXT,'second.txt','text/plain','another',dsn=dsn)
    assert normalize_next(dsn=dsn)
    ready=read_sources(owner,material,dsn=dsn)[0]
    assert ready['status']=='ready' and ready['page_count']==1
    assert not normalize_next(dsn=dsn)
    run=create_revision(owner,material,[job['normalization_id']],'run',settings,dsn=dsn)
    assert create_revision(owner,material,[job['normalization_id']],'run',settings,dsn=dsn).run_id==run.run_id
    assert run.input_source_set_id
    with pytest.raises(MaterialProcessingError,match='MATERIAL_RUN_INVALID'):
        create_material_processing_run(owner,material,run.source_artifact_id,'bypass',settings,dsn=dsn)
    with database_session(dsn) as session:
        frozen=deepcopy(session.get(MaterialSourceSet,run.input_source_set_id).manifest)
        artifact_ids=session.scalars(select(Artifact.artifact_id).where(Artifact.material_id==material)).all()
    claim=claim_next_material_processing_run(dsn=dsn);assert claim.run.run_id==run.run_id
    for stage in ('evidence','semantics','publishing'):_record_progress(run.run_id,stage,1,1,dsn=dsn)
    from pdf_evidence import material_pipeline
    monkeypatch.setattr(material_pipeline,'material_request_fits',lambda *_:True)
    with open_verified_artifact(owner,run.source_artifact_id,dsn=dsn) as pdf:
        source_path=tmp_path/'normalized-fixture.pdf';source_path.write_bytes(pdf.file.read());source_sha=pdf.sha256
    def semantics(_client,**request):
        handle=request['request']['sections'][0]['evidence'][0][0]
        return {'concepts':[{'k':'stack','l':'Stack','a':[],'c':[{'m':None,'s':[handle]}]}],'relations':[]}
    from runtime.source_resolver import _input
    source_request={'media_type':'application/pdf','source_path':str(source_path),'expected_source_sha256':source_sha}
    document=material_pipeline.analyze_material(source_request,
                settings,run_id=str(run.run_id),semantic_call=semantics,source_inputs=[source_request],input_binding=_input(owner,run.run_id,dsn=dsn))
    document=bind_structure_input(owner,run.run_id,document,dsn=dsn)
    published=publish_knowledge_structure(owner,material,run.run_id,document,dsn=dsn)
    assert published.document['schema']=='knowledge-structure/v4'
    assert published.view['schema']=='knowledge-structure-view/v3'
    evidence=document['evidence'][0]['evidence_id']
    source=resolve_evidence_source(owner,material,published.revision,evidence,dsn=dsn)
    assert source['format']=='txt' and source['normalized_page']==1 and '原文第' in source['label']
    assert source['original_url'].startswith('/v2/artifacts/')
    with pytest.raises(Exception):resolve_evidence_source(uuid4(),material,published.revision,evidence,dsn=dsn)
    assert read_knowledge_structure(owner,old_source.material_id,revision=old_structure['revision'],dsn=dsn).document==old_structure
    assert read_knowledge_structure(owner,material,revision=published.revision,dsn=dsn).document==document
    with database_session(dsn) as session:assert session.get(MaterialSourceSet,run.input_source_set_id).manifest==frozen
    assert request_material_discard(owner,material,dsn=dsn)=='removed'
    with database_session(dsn) as session:assert all(session.get(Artifact,a) is None for a in artifact_ids)
    assert read_knowledge_structure(owner,old_source.material_id,revision=old_structure['revision'],dsn=dsn).document==old_structure


def test_invalid_source_failure_is_durable_and_get_is_read_only(closed_loop,normalizer):
    owner=closed_loop[0].learner_id;dsn=closed_loop[4]
    material=create_draft(owner,'bad.txt','draft-invalid',dsn=dsn)
    upload_source(owner,material,b'\xff','bad.txt','text/plain','bad',dsn=dsn)
    assert normalize_next(dsn=dsn)
    before=read_sources(owner,material,dsn=dsn)
    assert before[0]['status']=='failed' and before[0]['error_code']=='UTF8_REQUIRED'
    assert read_sources(owner,material,dsn=dsn)==before
    assert not normalize_next(dsn=dsn)
    assert request_material_discard(owner,material,dsn=dsn)=='removed'


@pytest.mark.parametrize('extension',['.docx','.pptx','.txt','.md'])
def test_real_converter_inside_sandbox(extension,normalizer):
    import pymupdf
    if extension in ('.docx','.pptx'):data=(ROOT/'backend/tests/fixtures/normalization'/('sample'+extension)).read_bytes()
    else:data=(('# Text\n' if extension=='.md' else '')+'中文字與 code\n'+'long_line_'*40+'\n<script>never_execute</script>\n![remote](https://example.invalid/asset)\n').encode()
    pdf,mapping=convert(data,extension,MIME[extension],conversion_policy())
    with pymupdf.open(stream=pdf,filetype='pdf') as document:
        text=''.join(p.get_text() for p in document)
        assert mapping['page_count']==len(document)>0
        if extension=='.pptx':
            assert [r['origin_locator']['original_slide_number'] for r in mapping['records']]==[1,3]
            assert 'HIDDEN_SLIDE_MARKER' not in text and 'PRIVATE_NOTES_MARKER' not in text
        if extension=='.txt':assert text.replace('\n','').count('long_line_')==40
        if extension=='.md':assert '[圖片未載入]' in text and '<script>never_execute</script>' in text


def test_source_api_origin_owner_download_and_no_model(closed_loop,normalizer,monkeypatch):
    from fastapi.testclient import TestClient
    import runtime.api.app as api
    import httpx
    learner,_,settings,_,dsn,token=closed_loop
    attempts=[]
    def reject(*args,**kwargs):attempts.append('HTTP');raise AssertionError('MODEL_NOT_ALLOWED')
    monkeypatch.setattr(httpx.HTTPTransport,'handle_request',reject)
    app=api.create_app(api.ApiSettings(profile='local',public_origin='http://127.0.0.1:4173',secure_cookie=False,local_config=settings,dsn=dsn))
    client=TestClient(app);client.cookies.set('studydy_session',token)
    headers={'Origin':'http://127.0.0.1:4173','Idempotency-Key':'api-draft'}
    assert client.post('/v2/materials',json={'schema':'material-draft-create/v1','display_name':'source.md'}).status_code==403
    response=client.post('/v2/materials',json={'schema':'material-draft-create/v1','display_name':'source.md'},headers=headers)
    assert response.status_code==201,response.text
    material=response.json()['material_id'];path=f'/v2/materials/{material}/sources'
    upload={**headers,'Idempotency-Key':'api-upload','Content-Type':'text/markdown','X-Material-Name':'source.md'}
    received=client.post(path,content=b'# Stacks\n\nA stack follows LIFO order.\n',headers=upload)
    assert received.status_code==202,received.text
    original=received.json()['sources'][0]['original_artifact_id']
    own=client.get(f'/v2/artifacts/{original}')
    assert own.status_code==200 and own.headers['content-disposition'].startswith('attachment;')
    assert own.headers['x-content-type-options']=='nosniff' and own.headers['cache-control']=='private, no-store'
    assert client.get(f'/v1/artifacts/{original}').status_code==404
    foreign=TestClient(app)
    account=foreign.post('/v1/accounts',json={'email':'source_b@example.com','password':'Synthetic test password 42'},headers=headers)
    assert account.status_code==201
    assert foreign.get(path).status_code==404
    assert foreign.get(f'/v2/artifacts/{original}').status_code==404
    assert normalize_next(dsn=dsn)
    ready=client.get(path).json()['sources'][0]
    run=client.post(f'/v2/materials/{material}/revisions',json={'schema':'material-revision-create/v1','base_revision':None,'normalization_ids':[ready['normalization_id']]},headers={**headers,'Idempotency-Key':'revision'})
    assert run.status_code==202,run.text
    assert run.json()['schema']=='material-processing-run/v6' and run.json()['input_source_set_id']
    before=client.get(path).json()
    assert client.get(path).json()==before
    assert attempts==[]


def test_expired_normalization_lease_recovers_and_ready_is_immutable(closed_loop,normalizer):
    from datetime import UTC,datetime,timedelta
    from sqlalchemy.exc import DBAPIError
    owner=closed_loop[0].learner_id;dsn=closed_loop[4]
    material=create_draft(owner,'recover.txt','recover-draft',dsn=dsn)
    upload_source(owner,material,TEXT,'recover.txt','text/plain','recover-upload',dsn=dsn)
    identity=read_sources(owner,material,dsn=dsn)[0]['normalization_id']
    with database_session(dsn) as session:
        job=session.get(SourceNormalization,identity);job.status='running';job.lease_token=uuid4();job.lease_expires_at=datetime.now(UTC)-timedelta(seconds=1)
    assert normalize_next(dsn=dsn)
    ready=read_sources(owner,material,dsn=dsn)[0];assert ready['status']=='ready'
    with pytest.raises(DBAPIError,match='IMMUTABLE_SOURCE_BINDING'):
        with database_session(dsn) as session:session.get(SourceNormalization,identity).page_count=99
    assert read_sources(owner,material,dsn=dsn)[0]==ready
    assert request_material_discard(owner,material,dsn=dsn)=='removed'
    empty=create_draft(owner,'empty.txt','empty-draft',dsn=dsn)
    assert request_material_discard(owner,empty,dsn=dsn)=='removed'


def test_bundle_tampering_and_cross_owner_normalization_are_rejected(closed_loop,normalizer):
    from runtime.storage.tables import MaterialProcessingRun
    from runtime.source_resolver import _input
    owner=closed_loop[0].learner_id;settings=closed_loop[2];dsn=closed_loop[4]
    material=create_draft(owner,'tamper.txt','tamper-draft',dsn=dsn)
    upload_source(owner,material,TEXT,'tamper.txt','text/plain','tamper-upload',dsn=dsn);normalize_next(dsn=dsn)
    identity=read_sources(owner,material,dsn=dsn)[0]['normalization_id']
    other=create_draft(owner,'other.txt','other-draft',dsn=dsn)
    with pytest.raises(SourceError,match='SOURCE_NOT_READY'):create_revision(owner,other,[identity],'cross',settings,dsn=dsn)
    run=create_revision(owner,material,[identity],'tamper-run',settings,dsn=dsn)
    assert _input(owner,run.run_id,dsn=dsn)
    with database_session(dsn) as session:
        row=session.get(MaterialProcessingRun,run.run_id);changed=deepcopy(row.bundle_manifest);changed['canonical_sha256']='0'*64;row.bundle_manifest=changed
    with pytest.raises(SourceError,match='SOURCE_BINDING_INVALID'):_input(owner,run.run_id,dsn=dsn)


def test_replay_does_not_require_converter_or_current_model_config(closed_loop,normalizer,monkeypatch):
    owner=closed_loop[0].learner_id;settings=closed_loop[2];dsn=closed_loop[4]
    material=create_draft(owner,'replay.txt','replay-draft',dsn=dsn)
    source=upload_source(owner,material,TEXT,'replay.txt','text/plain','replay-upload',dsn=dsn)
    normalize_next(dsn=dsn);job=read_sources(owner,material,dsn=dsn)[0]
    run=create_revision(owner,material,[job['normalization_id']],'replay-run',settings,dsn=dsn)
    monkeypatch.delenv('STUDYDY_NORMALIZER_PYTHON')
    assert upload_source(owner,material,TEXT,'replay.txt','text/plain','replay-upload',dsn=dsn)==source
    assert create_revision(owner,material,[job['normalization_id']],'replay-run',{},dsn=dsn).run_id==run.run_id


def test_changed_conversion_policy_creates_new_job_without_rewriting_old_one(closed_loop,normalizer):
    from runtime.source_normalization import retry_normalization
    owner=closed_loop[0].learner_id;dsn=closed_loop[4]
    material=create_draft(owner,'policy.txt','policy-draft',dsn=dsn)
    upload_source(owner,material,TEXT,'policy.txt','text/plain','policy-upload',dsn=dsn)
    old=read_sources(owner,material,dsn=dsn)[0]['normalization_id']
    with database_session(dsn) as session:
        job=session.get(SourceNormalization,old);job.status='failed';job.error_code='NORMALIZER_VERSION_MISMATCH';job.policy={**job.policy,'version':0}
    retry_normalization(owner,material,old,dsn=dsn)
    latest=read_sources(owner,material,dsn=dsn)[0];assert latest['normalization_id']!=old
    assert normalize_next(dsn=dsn)
    assert read_sources(owner,material,dsn=dsn)[0]['status']=='ready'
    with database_session(dsn) as session:assert session.get(SourceNormalization,old).status=='failed'


def test_command_assessment_provenance_survives_configuration_removal(closed_loop,tmp_path,monkeypatch):
    import sys
    from test_closed_loop_v1 import generate_assessment,_assessment_response,Client
    from learning_adaptation.study_sessions import create_study_session
    from learning_adaptation.assessments import read_assessment
    from runtime.storage.tables import Assessment
    learner,source,settings,structure,dsn,_=closed_loop
    config={'schema':'semantic-command-config/v1','argv':[sys.executable,'-c','raise SystemExit(99)'],'model_id':'fixture-model',
            'model_revision':'fixture-revision'}
    path=tmp_path/'command.json';path.write_text(json.dumps(config));monkeypatch.setenv('STUDYDY_SEMANTIC_COMMAND_CONFIG',str(path))
    study=create_study_session(learner,source.material_id,structure['revision'],'command-study',dsn=dsn)
    concept=structure['concepts'][0]
    response=_assessment_response('fixture','教材中的 Stack 採用何種順序？',concept['evidence_refs'][0])
    assessment=generate_assessment(learner,study.study_session_id,concept['claims'][0]['claim_id'],'command-question',settings,
                dsn=dsn,client=Client(),semantic_call=lambda *_args,**_kwargs:response)
    with database_session(dsn) as session:
        provenance=session.get(Assessment,assessment.assessment_revision).generation_provenance
        assert provenance['schema']=='assessment-generation-provenance/v7'
        assert provenance['model_id']=='fixture-model' and provenance['execution_identity']['transport']=='command'
    monkeypatch.delenv('STUDYDY_SEMANTIC_COMMAND_CONFIG')
    restored=read_assessment(learner,study.study_session_id,assessment.assessment_revision,dsn=dsn)
    assert restored.assessment_revision==assessment.assessment_revision


@pytest.mark.parametrize('part',['word/embeddings/oleObject1.bin','word/activeX/activeX1.bin','word/vbaProject.bin'])
def test_embedded_active_office_parts_are_rejected_in_sandbox(part,normalizer):
    import zipfile
    from document_normalization.converter import NormalizationError
    original=ROOT/'backend/tests/fixtures/normalization/sample.docx'
    buffer=io.BytesIO()
    with zipfile.ZipFile(original) as old,zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as modified:
        for name in old.namelist():modified.writestr(name,old.read(name))
        modified.writestr(part,b'Synthetic active-object marker')
    with pytest.raises(NormalizationError,match='OFFICE_ACTIVE_OR_ENCRYPTED'):
        convert(buffer.getvalue(),'.docx',MIME['.docx'],conversion_policy())


def test_discard_waits_for_source_lease_then_cleans_all_artifacts(closed_loop,normalizer):
    from datetime import UTC,datetime,timedelta
    from runtime.material_discard import purge_discarded_material
    owner=closed_loop[0].learner_id;dsn=closed_loop[4]
    material=create_draft(owner,'discard.txt','discard-source-draft',dsn=dsn)
    upload_source(owner,material,TEXT,'discard.txt','text/plain','discard-source-upload',dsn=dsn)
    job_id=read_sources(owner,material,dsn=dsn)[0]['normalization_id']
    with database_session(dsn) as session:
        job=session.get(SourceNormalization,job_id);job.status='running';job.lease_token=uuid4();job.lease_expires_at=datetime.now(UTC)+timedelta(seconds=120)
    assert request_material_discard(owner,material,dsn=dsn)=='removing'
    assert not normalize_next(dsn=dsn)
    with database_session(dsn) as session:session.get(SourceNormalization,job_id).lease_expires_at=datetime.now(UTC)-timedelta(seconds=1)
    assert purge_discarded_material(owner,material,dsn=dsn)
    with database_session(dsn) as session:assert session.scalar(select(Artifact).where(Artifact.material_id==material)) is None


@pytest.mark.parametrize('extension,expected_pages',[('.doc',3),('.ppt',2)])
def test_legacy_office_source_is_saved_and_converted(closed_loop,normalizer,extension,expected_pages):
    owner=closed_loop[0].learner_id;dsn=closed_loop[4]
    material=create_draft(owner,'sample'+extension,'legacy-draft'+extension,dsn=dsn)
    data=(ROOT/'backend/tests/fixtures/normalization'/('sample'+extension)).read_bytes()
    upload_source(owner,material,data,'sample'+extension,MIME[extension],'legacy-upload'+extension,dsn=dsn)
    assert normalize_next(dsn=dsn)
    source=read_sources(owner,material,dsn=dsn)[0]
    assert source['status']=='ready' and source['page_count']==expected_pages
    with open_verified_artifact(owner,source['original_artifact_id'],dsn=dsn) as original:assert original.file.read()==data
    assert request_material_discard(owner,material,dsn=dsn)=='removed'

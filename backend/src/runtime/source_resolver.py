"""以 exact KS/run 綁定來源；原生定位可不確定，normalized PDF 不可指錯。"""
from copy import deepcopy
import json
from uuid import UUID
from sqlalchemy import select
from pdf_evidence.ocr_page_evidence import canonical_sha256
from knowledge_map.structure import _revision
from .storage.tables import MaterialProcessingRun,MaterialSourceSet,MaterialSourceSetItem,Artifact,SourceNormalization,database_session
from .storage.source_artifacts import open_verified_artifact
from .source_normalization import SourceError


def _input(owner,run_id,*,dsn=None):
    with database_session(dsn) as session:
        run=session.scalar(select(MaterialProcessingRun).where(MaterialProcessingRun.learner_id==owner,MaterialProcessingRun.run_id==run_id))
        if run is None:raise SourceError('RESOURCE_NOT_FOUND')
        if run.input_source_set_id is None:return None
        source_set=session.get(MaterialSourceSet,run.input_source_set_id)
        if source_set is None or source_set.learner_id!=owner or source_set.material_id!=run.material_id:raise SourceError('SOURCE_BINDING_INVALID')
        manifest=deepcopy(source_set.manifest);bundle=deepcopy(run.bundle_manifest)
        members=session.scalars(select(MaterialSourceSetItem).where(MaterialSourceSetItem.source_set_id==source_set.source_set_id).order_by(MaterialSourceSetItem.ordinal)).all()
        if (canonical_sha256(manifest)!=source_set.digest or canonical_sha256(bundle)!=run.bundle_manifest_sha256
            or bundle.get('source_set_digest')!=source_set.digest
            or len(members)!=len(manifest['items']) or not members):raise SourceError('SOURCE_BINDING_INVALID')
        if bundle.get('schema') not in {'bundle-manifest/v1','bundle-manifest/v2'}:raise SourceError('SOURCE_BINDING_INVALID')
        for ordinal,(member,item) in enumerate(zip(members,manifest['items']),1):
            if member.ordinal!=ordinal or str(member.source_id)!=item['source_id'] or str(member.normalization_id)!=item['normalization_id']:raise SourceError('SOURCE_BINDING_INVALID')
            job=session.get(SourceNormalization,member.normalization_id)
            if (job is None or job.status!='ready' or job.learner_id!=owner or job.material_id!=run.material_id
                or job.source_id!=member.source_id or canonical_sha256(job.policy)!=item['policy_sha256']
                or job.page_count!=item['page_count'] or str(job.normalized_artifact_id)!=item['normalized_artifact_id']
                or str(job.mapping_artifact_id)!=item['mapping_artifact_id']):raise SourceError('SOURCE_BINDING_INVALID')
            for prefix in ('original','normalized','mapping'):
                artifact=session.get(Artifact,UUID(item[prefix+'_artifact_id']))
                if artifact is None or artifact.learner_id!=owner or artifact.material_id!=run.material_id or bytes(artifact.sha256).hex()!=item[prefix+'_sha256']:raise SourceError('SOURCE_BINDING_INVALID')
        item=manifest['items'][0]
        if str(run.source_artifact_id)!=item['normalized_artifact_id']:raise SourceError('SOURCE_BINDING_INVALID')
        if bundle['schema']=='bundle-manifest/v1':
            if len(members)!=1 or bundle['canonical_sha256']!=item['normalized_sha256'] or bundle['canonical_artifact_id']!=str(run.source_artifact_id):raise SourceError('SOURCE_BINDING_INVALID')
        elif (bundle.get('processing_policy')!='source-boundary-incremental/v1'
              or bundle.get('source_names')!=[item['original_name'] for item in manifest['items']]):raise SourceError('SOURCE_BINDING_INVALID')
        expected_pages=[]
        for item in manifest['items']:
            for n in range(1,item['page_count']+1):
                expected_pages.append({'page':len(expected_pages)+1,'source_id':item['source_id'],'normalized_page':n})
        if bundle['pages']!=expected_pages:raise SourceError('SOURCE_BINDING_INVALID')
        binding={'schema':'structure-input-binding/v2' if bundle['schema']=='bundle-manifest/v2' else 'structure-input-binding/v1','source_set_id':str(source_set.source_set_id),'source_set_digest':source_set.digest,
                 'bundle_manifest_sha256':run.bundle_manifest_sha256,'manifest':manifest,'bundle':bundle}
        if binding['schema']=='structure-input-binding/v2':binding['base_revision']=run.base_revision
    for item in manifest['items']:
        for prefix in ('original','normalized','mapping'):
            with open_verified_artifact(owner,UUID(item[prefix+'_artifact_id']),dsn=dsn) as blob:
                if blob.sha256!=item[prefix+'_sha256']:raise SourceError('SOURCE_BINDING_INVALID')
    return binding


def bind_structure_input(owner,run_id,document,*,dsn=None):
    binding=_input(owner,run_id,dsn=dsn)
    if binding is None:return document
    result=deepcopy(document);result['schema']='knowledge-structure/v3';result['input_binding']=binding
    if binding['schema']=='structure-input-binding/v2':
        if result.pop('source_sha256')!=binding['source_set_digest']:raise SourceError('SOURCE_BINDING_INVALID')
        result['schema']='knowledge-structure/v4';result['source_set_sha256']=binding['source_set_digest']
    result['revision']=_revision(result)
    return result


def verify_structure_input(owner,run_id,document,*,dsn=None):
    expected=_input(owner,run_id,dsn=dsn)
    if document.get('input_binding')!=expected:raise SourceError('SOURCE_BINDING_INVALID')
    with database_session(dsn) as session:
        run=session.get(MaterialProcessingRun,run_id)
        if run is None or run.runtime_binding['model_id']!=document['provenance']['model_id'] or run.runtime_binding['model_revision']!=document['provenance']['model_revision']:raise SourceError('SOURCE_BINDING_INVALID')
        expected_execution=run.runtime_binding['semantic_service'] if run.runtime_binding['schema']=='material-runtime-binding/v2' else None
        if document.get('execution_identity')!=expected_execution:raise SourceError('SOURCE_BINDING_INVALID')


def resolve_evidence_source(owner,material_id,revision,evidence_id,*,dsn=None):
    from .storage.knowledge_structures import read_knowledge_structure
    stored=read_knowledge_structure(owner,material_id,revision=revision,dsn=dsn)
    document=stored.document
    evidence=next((e for e in document['evidence'] if e['evidence_id']==evidence_id),None)
    if evidence is None:raise SourceError('RESOURCE_NOT_FOUND')
    page=evidence['page'];binding=document.get('input_binding')
    if binding is None:
        with database_session(dsn) as session:
            run=session.get(MaterialProcessingRun,UUID(document['run_id']));artifact=str(run.source_artifact_id)
        return {'schema':'evidence-source/v1','format':'pdf','original_name':'原始 PDF','original_url':f'/v1/artifacts/{artifact}',
                'preview_url':f'/v1/artifacts/{artifact}#page={page}','normalized_page':page,'accuracy':'exact','origin_locators':[{'original_page':page}],
                'label':f'原始教材第 {page} 頁'}
    location=binding['bundle']['pages'][page-1]
    item=next(item for item in binding['manifest']['items'] if item['source_id']==location['source_id'])
    page=location['normalized_page']
    with open_verified_artifact(owner,UUID(item['mapping_artifact_id']),dsn=dsn) as opened:mapping=json.loads(opened.file.read())
    if mapping['original_sha256']!=item['original_sha256'] or mapping['normalized_sha256']!=item['normalized_sha256']:raise SourceError('SOURCE_BINDING_INVALID')
    region=evidence['source_locator']['region']
    def overlaps(candidate):
        box=candidate.get('region')
        return box is None or (min(box[2],region[2])>max(box[0],region[0]) and min(box[3],region[3])>max(box[1],region[1]))
    records=[r for r in mapping['records'] if (r.get('normalized_page')==page and overlaps(r))
             or any(c['normalized_page']==page and overlaps(c) for c in r.get('candidates',[]))]
    accuracy='exact' if records and all(r['accuracy']=='exact' for r in records) else 'ambiguous' if records else 'unavailable'
    locators=[r['origin_locator'] for r in records]
    label=f'轉換後第 {page} 頁'
    if mapping['format']=='pdf':label=f'原始教材第 {page} 頁'
    elif mapping['format']=='pptx' and locators:label=f"原教材第 {locators[0]['original_slide_number']} 張投影片（轉換後第 {page} 頁）"
    elif mapping['format'] in ('txt','md') and locators:label=f"原文第 {min(l['line_start'] for l in locators)}–{max(l['line_end'] for l in locators)} 行（轉換後第 {page} 頁）"
    return {'schema':'evidence-source/v1','format':mapping['format'],'original_name':item['original_name'],
            'original_url':f"/v2/artifacts/{item['original_artifact_id']}",
            'preview_url':f"/v1/artifacts/{item['normalized_artifact_id']}#page={page}",
            'normalized_page':page,'accuracy':accuracy,'origin_locators':locators,'label':label}

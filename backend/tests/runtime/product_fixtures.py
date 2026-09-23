"""以現行來源集合契約建立合成資料；不使用舊單 PDF 上傳或處理入口。"""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from pathlib import Path
import json
from unittest.mock import patch
from uuid import UUID
import pymupdf
from sqlalchemy import select
from runtime import source_normalization as normalization
from runtime.source_revisions import create_revision
from runtime.source_resolver import _input, bind_structure_input
from runtime.storage.tables import Material, MaterialProcessingRun, SourceNormalization, database_session
from runtime.storage.knowledge_structures import publish_knowledge_structure
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.source_set import rebase_page
from knowledge_map.structure import build_document_context, SemanticState, apply_semantic_response, build_structure_draft


def seed_pdf(owner, stream, key, *, dsn, display_name=None):
    data=stream.read();name=display_name or 'Synthetic.pdf'
    material=normalization.create_draft(owner,name,key,dsn=dsn)
    def identity(data, *_):
        with pymupdf.open(stream=data,filetype='pdf') as pdf:count=len(pdf)
        digest=sha256(data).hexdigest()
        return data,{'schema':'source-mapping/v1','format':'pdf','original_sha256':digest,'normalized_sha256':digest,'page_count':count,
            'records':[{'normalized_page':i,'origin_locator':{'original_page':i},'accuracy':'exact'} for i in range(1,count+1)]}
    with patch.object(normalization,'conversion_policy',return_value={'schema':'normalization-policy/v1','renderer':'fixture-pdf'}):
        source=normalization.upload_source(owner,material,data,name,'application/pdf',key,dsn=dsn)
    with patch.object(normalization,'convert',side_effect=identity):
        while True:
            items=normalization.read_sources(owner,material,dsn=dsn)
            item=next(i for i in items if i['source_id']==source)
            if item['status']=='ready':break
            assert normalization.normalize_next(dsn=dsn)
    return SimpleNamespace(material_id=material,artifact_id=item['normalized_artifact_id'],sha256=sha256(data).hexdigest(),size_bytes=len(data))


def seed_run(owner, material, artifact, key, settings, *, dsn):
    with database_session(dsn) as session:base=session.get(Material,material).head_revision
    ids=[s['normalization_id'] for s in normalization.read_sources(owner,material,dsn=dsn)] if base is None else []
    config=deepcopy(settings)
    if base is not None:
        config['runtime_lock']['material_review']=json.loads((Path(__file__).parents[3]/'local_ai/runtime-lock.json').read_text())['material_review']
    return create_revision(owner,material,ids,key,config,base_revision=base,dsn=dsn)


def publish_fixture_structure(owner, material, run, document, *, dsn, **kwargs):
    binding=_input(owner,run,dsn=dsn)
    if document.get('input_binding') != binding:
        # fixture 的來源身分須重新綁到本次 SourceSet；草稿沒有正式 schema 或 revision。
        rows=document['evidence'];pages=[]
        for number in range(1,document['page_count']+1):
            blocks=[{'kind':e['kind'],'source':e['source'],'text':e['exact_text'],'reading_order':e['block_order'],
                'locator':deepcopy(e['source_locator'])} for e in rows if e['page']==number]
            if blocks:pages.append(rebase_page({'schema':'page-evidence/v1','evidence_blocks':blocks},binding['source_set_digest'],number))
        context=build_document_context(pages,page_count=document['page_count'],source_pages=binding['bundle']['pages'])
        indexes={e['evidence_id']:i for i,e in enumerate(rows)}
        keys={c['concept_id']:f'concept_{i}' for i,c in enumerate(document['concepts'])}
        response={'concepts':[{'k':keys[c['concept_id']],'l':c['label'],'a':c['aliases'],
            'c':[{'m':cl['text'],'s':[indexes[e] for e in cl['evidence_refs']]} for cl in c['claims']]} for c in document['concepts']],
            'relations':[{'s':keys[r['source_concept_id']],'t':keys[r['target_concept_id']],'k':r['type'],'r':r['learner_reason'],
                'e':[indexes[e] for e in r['evidence_refs']],'c':r['confidence']} for r in document['relations']]}
        state=SemanticState();state.rejected_claims=document['metrics']['rejected_claims']
        state.source_review_required=document.get('source_review_required',False)
        apply_semantic_response(response,context=context,bundle={'sections':context['sections'],'evidence':context['evidence']},state=state)
        with database_session(dsn) as session:
            provenance=deepcopy(session.get(MaterialProcessingRun,run).runtime_binding)
        result=build_structure_draft(context,state,source_sha256=binding['source_set_digest'],run_id=str(run),
            produced_at=document['produced_at'],runtime_lock_sha256=provenance['runtime_lock_sha256'],model_id=provenance['model_id'],
            model_revision=provenance['model_revision'],semantic_calls=document['metrics']['semantic_calls'],ocr_calls=document['metrics']['ocr_calls'])
        result=bind_structure_input(owner,run,result,dsn=dsn);document.clear();document.update(result)
    return publish_knowledge_structure(owner,material,run,document,dsn=dsn,**kwargs)

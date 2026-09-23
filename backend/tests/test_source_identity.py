from structure_fixtures import build_knowledge_structure
from copy import deepcopy
import pytest

from knowledge_map.source_identity import unchanged_claims, seed_incremental_state
from knowledge_map.structure import SemanticState, build_document_context, apply_semantic_response
from pdf_evidence.source_set import rebase_page
from test_knowledge_structure_v1 import _page, _block, RUN_ID, PRODUCED_AT, MODEL_REVISION


def structure(context, state, digest):
    return build_knowledge_structure(context, state, source_sha256=digest, run_id=RUN_ID,
        produced_at=PRODUCED_AT, runtime_lock_sha256='0'*64, model_id='google/gemma-4-31B-it-qat-w4a16-ct',
        model_revision=MODEL_REVISION, semantic_calls=1, ocr_calls=0)


@pytest.mark.parametrize('binding',[None,[], 'invalid'])
def test_v1_rejects_malformed_source_binding(binding):
    from knowledge_map.structure import validate_knowledge_structure, _revision
    context=build_document_context([_page(1,[_block(1,0,'paragraph','A stack uses LIFO.')])],page_count=1)
    document=structure(context,SemanticState(),'1'*64)
    document['input_binding'] = binding
    document['revision']=_revision(document)
    assert validate_knowledge_structure(document) is False


def test_source_local_identity_survives_new_set_but_changed_claims_do_not_inherit():
    page = _page(1, [_block(1,0,'paragraph','A stack removes the last inserted value first.')])
    context = build_document_context([page],page_count=1)
    state = SemanticState()
    apply_semantic_response({'concepts':[{'k':'stack','l':'Stack','a':[],'c':[{'m':None,'s':[0]}]}], 'relations':[]},
        context=context,bundle={'evidence':context['evidence']},state=state)
    old = structure(context,state,'1'*64)
    binding={'source_set_digest':'2'*64,'manifest':{'items':[{'source_id':'A','original_sha256':'1'*64,'normalized_sha256':'1'*64}]},
        'bundle':{'pages':[{'page':1,'source_id':'A','normalized_page':1}]}}
    old['input_binding']={**deepcopy(binding),'source_set_digest':'1'*64}
    snapshot=deepcopy(old)
    updated_context=build_document_context([rebase_page(page,'2'*64,1)],page_count=1)
    seeded=seed_incremental_state(old,updated_context,binding)
    updated=structure(updated_context,seeded,'2'*64)
    updated['input_binding']=binding
    matches=unchanged_claims(old,updated)
    assert len(matches)==1
    assert next(iter(matches))[1]!=next(iter(matches.values()))[1]
    assert old==snapshot
    changed=deepcopy(updated)
    changed['concepts'][0]['claims'][0]['text']='A stack removes the first inserted value first.'
    assert unchanged_claims(old,changed)=={}
    duplicated=deepcopy(updated)
    duplicated['concepts'].append(deepcopy(duplicated['concepts'][0]))
    assert unchanged_claims(old,duplicated)=={}

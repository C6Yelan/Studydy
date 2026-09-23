"""單一觀念題組的合成資料與受控模型 helpers；不匯入 test modules。"""
import io
import re
from uuid import uuid4

import psycopg
import pymupdf

from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context, build_knowledge_structure
from learning_adaptation import assessment_sets as sets
from learning_adaptation.study_sessions import create_study_session
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.material_processing import claim_next_material_processing_run, _record_progress
from runtime.semantic_service import SemanticServiceError
from product_fixtures import seed_pdf, seed_run, publish_fixture_structure


def concept_fixture(closed_loop, count=3, *, facts=None, label="Signals", evidence_kind="paragraph", source_review_required=False):
    learner, _, settings, _, dsn, token = closed_loop
    points = [f'Signal {index} uses code{index}.' for index in range(count)] if facts is None else facts
    count = len(points)
    facts = [*points, 'Other topic uses EXTERNAL.']
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for index, value in enumerate(facts):
            page.insert_text((72, 72 + index * 22), value)
        regions = [list(page.search_for(value)[0]) for value in facts]
        payload = pdf.tobytes()
    source = seed_pdf(learner.learner_id, io.BytesIO(payload), str(uuid4()), dsn=dsn)
    run = seed_run(learner.learner_id, source.material_id, source.artifact_id,
                                        str(uuid4()), settings, dsn=dsn)
    assert claim_next_material_processing_run(dsn=dsn).run.run_id == run.run_id
    for stage in ('evidence','semantics','publishing'):
        _record_progress(run.run_id, stage, 1, 1, dsn=dsn)
    page_ref = 'page:sha256:' + canonical_sha256({'source_sha256': source.sha256, 'page_number': 1})
    blocks = []
    for index, value in enumerate(facts):
        region = regions[index]
        block_id = 'block:sha256:' + canonical_sha256({'page_ref':page_ref,'reading_order':index,'region':region})
        content = {'page_ref':page_ref,'block_id':block_id,'kind':evidence_kind,'source':'native_text',
                   'text':value,'reading_order':index,'region':region}
        blocks.append({'evidence_id':'evidence:sha256:'+canonical_sha256(content),'block_id':block_id,
            'kind':evidence_kind,'source':'native_text','text':value,'reading_order':index,
            'locator':{'page':1,'block_id':block_id,'region':region}})
    page = {'schema':'page-evidence/v4','material_id':'material:sha256:'+source.sha256,
            'page_ref':page_ref,'page_number':1,'evidence_blocks':blocks}
    context = build_document_context([page], page_count=1)
    state = SemanticState()
    state.source_review_required = source_review_required
    apply_semantic_response({'concepts':[
        {'k':'signals','l':label,'a':[],'c':[{'m':None,'s':[index]} for index in range(count)]},
        {'k':'other','l':'Other topic','a':[],'c':[{'m':None,'s':[count]}]},
    ],'relations':[]}, context=context, bundle={'sections':context['sections'],'evidence':context['evidence']},state=state)
    lock = settings['runtime_lock']
    document = build_knowledge_structure(context,state,source_sha256=source.sha256,run_id=str(run.run_id),
        produced_at='2026-09-20T00:00:00+00:00',runtime_lock_sha256=canonical_sha256(lock),
        model_id=lock['semantic_service']['model_id'],model_revision=lock['semantic_service']['revision'],semantic_calls=1,ocr_calls=0)
    publish_fixture_structure(learner.learner_id,source.material_id,run.run_id,document,dsn=dsn)
    concept = next(item for item in document['concepts'] if item['label']==label)
    study = create_study_session(learner,source.material_id,document['revision'],str(uuid4()),
                                 current_concept_id=concept['concept_id'],dsn=dsn)
    return {'learner':learner,'settings':settings,'dsn':dsn,'token':token,'source':source,
            'run':run,'document':document,'concept':concept,'study':study}


def model_for(fixture, *, fail=(), calls=None):
    answers = {}
    calls = [] if calls is None else calls
    def model(_client, **kwargs):
        calls.append(kwargs['task'])
        # 真正的另一個 connection 可立即取得 session 鎖，證明推論沒有包在長交易裡。
        with psycopg.connect(fixture['dsn']) as connection:
            connection.execute('SELECT study_session_id FROM study_sessions WHERE study_session_id=%s FOR UPDATE NOWAIT',
                               (fixture['study'].study_session_id,))
        if kwargs['task']=='assessment':
            request = kwargs['request']
            match = re.search(r'Signal (\d+) uses',request['claim']['text'])
            assert match is not None,'題組不可跨到其他觀念'
            number = int(match[1])
            if number in fail:
                raise SemanticServiceError('SEMANTIC_SERVICE_UNAVAILABLE')
            candidate={'learning_angle':'signal code','novelty':'distinct','safety':'safe',
                'prompt':f'Which code does Signal {number} use?','correct_answer':f'code{number}',
                'supporting_evidence_ids':[request['claim']['evidence'][0]['evidence_id']],
                'distractors':[f'wrong{number}a',f'wrong{number}b',f'wrong{number}c']}
            answers[candidate['prompt']]=candidate['correct_answer']
            return {'schema':'assessment-semantics-response/v2','candidates':[
                candidate,{**candidate,'safety':'reject'},{**candidate,'safety':'reject'}]}
        return {'schema':'assessment-check-response/v2','verdicts':[
            {'question_index':question['question_index'],'answer_status':'unique',
             'selected_option_index':question['options'].index(answers[question['prompt']]),
             'duplicate_prior_index':None,'quality_issues':[]}
            for question in kwargs['request']['questions']]}
    return model


def finish(fixture, *, fail=(), calls=None):
    model = model_for(fixture,fail=fail,calls=calls)
    while work := sets.claim_set_work(dsn=fixture['dsn']):
        sets.execute_set_work(work,dsn=fixture['dsn'],semantic_call=model)


def create(fixture, key='round'):
    return sets.create_set(fixture['learner'],fixture['study'].study_session_id,
        fixture['concept']['concept_id'],key,fixture['settings'],dsn=fixture['dsn'])


def read(fixture, identity):
    return sets.read_set(fixture['learner'],fixture['study'].study_session_id,identity,dsn=fixture['dsn'])

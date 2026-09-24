"""透過 B05 題組檢查品質排序／重複題目與 provenance，不使用已刪除的單題 API。"""
from copy import deepcopy
from learning_adaptation import assessment_sets as sets
from learning_adaptation.answer_events import read_answer_events
from test_assessment_sets import closed_loop, concept_fixture, create, read, model_for


def test_partial_review_flag_keeps_safe_recall_and_duplicate_does_not_score(closed_loop):
    f=concept_fixture(closed_loop,2,source_review_required=True)
    identity=create(f);base=model_for(f);observed=[]
    def model(client,**kw):
        observed.append(deepcopy(kw));result=base(client,**kw)
        if kw['task']=='assessment_check':
            for verdict in result['verdicts']:
                verdict['quality_issues']=['weak_distractors','uneven_options']
                if kw['request']['prior_questions']:verdict['duplicate_prior_index']=0
        return result
    while work:=sets.claim_set_work(dsn=f['dsn']):sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model)
    group=read(f,identity)
    assert group['status']=='partial_ready' and group['verified_count']==1
    assert read_answer_events(f['learner'],f['study'].study_session_id,dsn=f['dsn'])==()
    assert any(kw['request'].get('prior_questions') for kw in observed)


def test_large_prior_context_is_bounded_and_exact_duplicate_is_rejected(closed_loop):
    f=concept_fixture(closed_loop,1);prompts=[];observed=[]
    def publish(prompt):
        identity=create(f,key=f'round-{len(prompts)}');base=model_for(f);names={}
        def model(client,**kw):
            observed.append(deepcopy(kw))
            if kw['task']=='assessment':
                result=base(client,**kw)
                for candidate in result['candidates']:
                    names[prompt]=candidate['prompt'];candidate['prompt']=prompt
                return result
            request=deepcopy(kw['request'])
            for question in request['questions']:question['prompt']=names[question['prompt']]
            return base(client,**{**kw,'request':request})
        while work:=sets.claim_set_work(dsn=f['dsn']):sets.execute_set_work(work,dsn=f['dsn'],semantic_call=model)
        return identity,read(f,identity)
    for i in range(4):
        prompt=f'情境 {i}：'+ '教材內容'*500+' Signal 0 uses which code?'
        observed.clear();identity,group=publish(prompt)
        assert group['status']=='ready'
        request=next(kw['request'] for kw in observed if kw['task']=='assessment')
        assert len(request['prior_questions'])<=1
        if prompts:assert request['prior_questions'][0]['prompt']==prompts[-1]
        prompts.append(prompt)
        from test_assessment_remediation import answer
        answer(f,identity)
    observed.clear();_,group=publish(prompts[0])
    assert group['status']=='failed' and group['verified_count']==0
    assert all(kw['task']=='assessment' for kw in observed)

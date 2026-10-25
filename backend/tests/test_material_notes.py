"""來源、全份涵蓋、重點整理的概念涵蓋與註記精確對應的回歸。"""
from copy import deepcopy
import pytest
from runtime import note_content as nc, material_notes as mn


def concepts():
    return [{"concept_id": "c1", "label": "訊號", "claims": [
        {"claim_id": f"q{i}", "text": f"第 {i} 個重點", "evidence": [{"evidence_id": f"e{i}", "quote": f"原文 {i}", "page": i + 1}]}
        for i in range(3)]}]


def response(request):
    if request.get("phase") == "review_paragraph":
        return {"checks": [{"part": part, "supported": True, "reason": ""} for part in request["parts"]],
            "coverage": [{"claim": ref, "explained": True, "note_parts": ["text"], "reason": ""} for ref in request["coverage_claims"]]}
    return {"paragraphs": [{"heading": c["label"], "text": q["text"], "table": None, "formula": None,
        "claim_ids": [q["claim_id"]], "evidence_ids": q.get("evidence_ids") or [e["evidence_id"] for e in q["evidence"]]}
        for c in request["concepts"] for q in c["claims"]]}



def full(values, call):
    regions = [{"label": "區域", "concept_ids": list(dict.fromkeys(c["concept_id"] for c in values))}]
    return mn.complete_sections(values, mn.plan_sections(values, regions), call, [], lambda *args: None)


def test_complete_notes_preserve_all_claims_and_source_pages():
    result = full(concepts(), response)
    assert [p["id"] for p in result] == ["0", "1", "2"]
    assert {x for p in result for x in p["claim_ids"]} == {"q0", "q1", "q2"}
    assert [p["evidence"][0]["page"] for p in result] == [1, 2, 3]


def test_saved_page_headers_are_explicit_citations_without_cross_source_or_page_context():
    values=concepts()
    values[0]['claims']=values[0]['claims'][:1]
    base=values[0]['claims'][0]['evidence'][0]
    base.update(source_id='source-a',normalized_page=1,page_ref='page-1',block_order=2,
        kind='paragraph',source='native_text',source_locator={'page':1,'region':[0,20,100,40]})
    before=deepcopy(values)
    def item(identity,page,label):
        return {**base,'evidence_id':identity,'page':page,'exact_text':label,'block_order':1}
    document={'evidence':[item('e0',1,'原文 0'),item('header',1,'原文欄名'),
        item('foreign',2,'另一檔欄名'),item('next-page',3,'下一頁欄名')],
        'input_binding':{'manifest':{'items':[{'source_id':'source-a','original_name':'A.pdf'},
            {'source_id':'source-b','original_name':'B.pdf'}]},'bundle':{'pages':[
                {'source_id':'source-a','normalized_page':1},
                {'source_id':'source-b','normalized_page':1},
                {'source_id':'source-a','normalized_page':2}]}}}
    enriched=nc.with_page_context(values,document)
    assert values==before
    assert [e['evidence_id'] for e in enriched[0]['claims'][0]['context_evidence']]==['header']
    request,mapping=mn.model_request(enriched,'full')
    assert request['concepts'][0]['claims'][0]['context_evidence_ids']==['E2']
    raw=response(request)
    raw['paragraphs'][0]['evidence_ids'].append('E2')
    result=nc.validate(mn.canonical_response(raw,mapping),enriched,'full')
    assert [e['evidence_id'] for e in result[0]['evidence']]==['e0','header']
    assert result[0]['evidence'][1]['quote']=='原文欄名'
    raw['paragraphs'][0]['evidence_ids']=['E2']
    with pytest.raises(nc.ReferenceInvalid):
        nc.validate(mn.canonical_response(raw,mapping),enriched,'full')
    empty={**document,'evidence':[document['evidence'][0]]}
    assert nc.with_page_context(values,empty)[0]['claims'][0]['context_evidence']==[]
    same_block=deepcopy(empty)
    same_block['evidence'][0]['exact_text']='同區塊表頭：原文 0'
    expanded=nc.with_page_context(values,same_block)
    request,mapping=mn.model_request(expanded,'full')
    assert request['concepts'][0]['claims'][0]['evidence_ids']==['E1']
    assert request['concepts'][0]['claims'][0]['context_evidence_ids']==['E1']
    result=nc.validate(mn.canonical_response(response(request),mapping),expanded,'full')
    assert {e['quote'] for e in result[0]['evidence']}=={'原文 0','同區塊表頭：原文 0'}


@pytest.mark.parametrize("failure", ["foreign_claim", "foreign_evidence", "wrong_claim_evidence", "missing_claim", "ragged_table"])
def test_invalid_model_output_is_not_published(failure):
    value = response({"concepts": concepts()})
    if failure == "foreign_claim": value["paragraphs"][0]["claim_ids"] = ["foreign"]
    if failure == "foreign_evidence": value["paragraphs"][0]["evidence_ids"] = ["foreign"]
    if failure == "wrong_claim_evidence": value["paragraphs"][0]["evidence_ids"] = ["e1"]
    if failure == "missing_claim": value["paragraphs"].pop()
    if failure == "ragged_table": value["paragraphs"][0]["table"] = {"headers": ["a", "b"], "rows": [["only one"]]}
    with pytest.raises(ValueError): nc.validate(value, concepts(), "full")


def test_regeneration_matches_only_identical_points_and_sources_and_never_duplicates_annotation():
    original = full(concepts(), response)
    annotations = [{"anchor": original[0]["anchor"], "body": "我的想法", "original_paragraph": deepcopy(original[0])}]
    new = deepcopy(original)
    new[0]["text"] = "重新組織的說明"
    assert nc.annotation_matches(new, annotations)[0]["paragraph_id"] == "0"
    changed = concepts()
    changed[0]["claims"][0]["text"] += "新增條件"
    replaced = full(changed, response)
    orphaned = nc.annotation_matches(replaced, annotations)[0]
    assert orphaned["paragraph_id"] is None
    assert orphaned["body"] == "我的想法" and orphaned["original_paragraph"] == original[0]
    assert nc.annotation_matches([*original, original[0]], annotations)[0]["paragraph_id"] is None


def test_full_notes_batch_without_omitting_points(monkeypatch):
    values = concepts()
    unit = {**values[0], "claims": values[0]["claims"][:1]}
    monkeypatch.setattr(mn, "BATCH_TOKENS", mn.estimate_prompt_tokens(nc.PROMPT, mn.model_request([unit], "full")[0]) + 1)
    calls = []
    def answer(request):
        calls.append(request)
        return response(request)
    result = full(values, answer)
    assert len(result) == 3
    assert len([r for r in calls if r.get("phase") == "review_paragraph"]) == 3
    assert len([r for r in calls if r.get("phase") != "review_paragraph"]) == 3
    assert {v for p in result for v in p["claim_ids"]} == {"q0", "q1", "q2"}


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_invalid_response_exhausts_three_batches_with_one_correction_each(kind):
    calls, diagnostics, saved, results = [], [], [], []
    values = concepts()
    plan = mn.plan_sections(values, [{"label": "區域", "concept_ids": ["c1"]}], kind)
    def answer(request):
        calls.append(request)
        return {"paragraphs": []}
    with pytest.raises(ValueError, match="^NOTE_FORMAT_INVALID$"):
        mn.complete_sections(values, plan, answer, results, lambda *args: saved.append(args), diagnostics, kind)
    assert len(calls) == 6
    assert ["correction" in request for request in calls] == [False, True] * 3
    assert diagnostics[-1] == {"event": "part_attempts", "part": 0, "attempts": 3,
        "failed": ["NOTE_FORMAT_INVALID"] * 3, "succeeded": False}
    assert saved == results == []


def test_notes_contract_preserves_old_runtime_snapshots():
    from runtime.local_ai_config import configured_lock
    from runtime.material_runtime import validate_runtime_lock
    from content_identity import canonical_sha256
    current = configured_lock()
    old = deepcopy(current)
    old.pop("material_notes")
    old_hash = canonical_sha256(old)
    assert validate_runtime_lock(old) == old
    assert canonical_sha256(old) == old_hash
    assert validate_runtime_lock(current) == current
    assert current["material_notes"]["prompt"] == nc.PROMPT


@pytest.mark.parametrize("change", [
    {"version": "material-notes/v10"},
    {"version": "material-notes/v10", "summary_layout": "invalid"},
    {"version": "material-notes/v12"},
])
def test_invalid_historical_notes_contract_is_rejected(change):
    from runtime.local_ai_config import configured_lock
    from runtime.material_runtime import validate_runtime_lock
    from runtime.material_runtime import MaterialAnalysisError
    lock = deepcopy(configured_lock())
    lock["material_notes"].update(change)
    with pytest.raises(MaterialAnalysisError, match="RUNTIME_LOCK_INVALID"):
        validate_runtime_lock(lock)


def test_notes_generation_rejects_historical_policy(monkeypatch):
    lock = deepcopy(mn.configured_lock())
    lock["material_notes"].update(version="material-notes/v10", summary_layout="a4-12pt-18mm/v1")
    claim = {"lock": lock, "key": (None, None, None, "summary")}
    saved = []
    monkeypatch.setattr(mn, "_claim", lambda *args, **kwargs: claim)
    monkeypatch.setattr(mn, "_save", lambda *args, **kwargs: saved.append(kwargs))
    assert mn.step(call=lambda _: pytest.fail("歷史筆記契約不可呼叫模型"))
    assert saved[0]["error"] == "NOTE_CONTRACT_INVALID"


def test_shared_claim_is_explained_once_with_all_its_concept_links():
    values = concepts()
    values.append({"concept_id": "c2", "label": "另一概念", "claims": [deepcopy(values[0]["claims"][0])]})
    canonical = response({"concepts": values[:1]})
    result = nc.validate(canonical, values, "full")
    assert [c["concept_id"] for c in result[0]["concepts"]] == ["c1", "c2"]
    request, identities = mn.model_request(values, "full")
    assert len(request["evidence"]) == 3
    assert len(identities["claims"]) == 3
    assert len(request["concepts"]) == 2


def test_same_evidence_distinct_quotes_stay_complete_and_anchor_survives_order_ids_and_restart():
    import json, os, subprocess, sys
    values=concepts()
    values[0]['claims']=values[0]['claims'][:2]
    for i,q in enumerate(values[0]['claims']):
        q['evidence'][0].update(evidence_id='shared',source_id='immutable-source',normalized_page=1,quote=f'quote {i}')
    raw={'paragraphs':[{'heading':'共同來源','text':'兩個重點各有原文依據。','table':None,'formula':None,
        'claim_ids':['q0','q1'],'evidence_ids':['shared']}]}
    original=nc.validate(raw,values,'full')[0]
    assert {e['quote'] for e in original['evidence']}=={'quote 0','quote 1'}
    reordered=deepcopy(values)
    reordered[0]['claims'].reverse()
    reversed_raw=deepcopy(raw)
    reversed_raw['paragraphs'][0]['claim_ids'].reverse()
    assert nc.validate(reversed_raw,reordered,'full')[0]['anchor']==original['anchor']
    rebased=deepcopy(values)
    for i,q in enumerate(rebased[0]['claims']):
        q['claim_id']=f'new-q{i}'
        q['evidence'][0].update(evidence_id='rebased-shared',page=99,page_ref='new-page')
    new_raw=deepcopy(raw)
    new_raw['paragraphs'][0].update(claim_ids=['new-q0','new-q1'],evidence_ids=['rebased-shared'])
    assert nc.validate(new_raw,rebased,'full')[0]['anchor']==original['anchor']
    script='import json,sys; from runtime.note_content import validate; v=json.load(sys.stdin); print(validate(v["raw"],v["concepts"],"full")[0]["anchor"])'
    for seed in ('1','3'):
        result=subprocess.check_output([sys.executable,'-c',script],input=json.dumps({'raw':raw,'concepts':values}).encode(),env={**os.environ,'PYTHONHASHSEED':seed})
        assert result.decode().strip()==original['anchor']


@pytest.mark.parametrize('bad_part', ['text', 'formula', 'table.row.0', 'claim.C1'])
def test_content_mismatch_regenerates_the_paragraph_and_rechecks_every_part(bad_part):
    values=concepts()[:1]
    values[0]['claims']=values[0]['claims'][:1]
    calls=[]
    writers=[]
    def model(request):
        calls.append(request)
        if request.get('phase')=='review_paragraph':
            verdict={**response(request),'checks':[{'part':part,'supported':not (len(writers)==1 and part==bad_part),
                'reason':'該條件與數值的配對不受同一範例支持。' if len(writers)==1 and part==bad_part else ''}
                for part in request['parts']]}
            if len(writers)==1 and bad_part=='claim.C1':
                verdict['coverage'][0].update(explained=False,note_parts=[],reason='正文漏掉重點結論。')
            return verdict
        writers.append(request)
        result=response(request)
        result['paragraphs'][0].update(formula='來源例中的值',table={'headers':['項目','來源內容'],'rows':[['甲','範例甲的值']]})
        return result
    result=full(values,model)
    assert len(writers)==2 and len(calls)==4
    feedback=writers[1]['correction']['content_review']
    assert feedback['issues'][0]['part']==bad_part
    assert writers[1]['correction']['problem']=='NOTE_CONTENT_UNSUPPORTED'
    assert result and calls[-1]['phase']=='review_paragraph'
    assert calls[-1]['parts']==['heading','text','formula','table.headers','table.row.0']


@pytest.mark.parametrize('bad_response', ['missing_part','duplicate_part','string_boolean','empty_checks'])
def test_content_review_rejects_missing_or_malformed_verdicts(bad_response):
    raw=response({'concepts':concepts()})
    paragraph=nc.validate(raw,concepts(),'full')[0]
    def model(request):
        checks=[{'part':p,'supported':True,'reason':''} for p in request['parts']]
        if bad_response=='missing_part':checks.pop()
        if bad_response=='duplicate_part':checks[-1]=checks[0]
        if bad_response=='string_boolean':checks[0]['supported']='true'
        if bad_response=='empty_checks':checks=[]
        return {**response(request),'checks':checks}
    with pytest.raises(nc.ReviewInvalid,match='NOTE_REVIEW_INVALID'):
        nc.review_paragraph(paragraph,model,claims={q['claim_id']:q for c in concepts() for q in c['claims']},kind='full')


def test_content_review_sees_only_cited_original_and_keeps_all_table_rows():
    values=concepts()
    raw=response({'concepts':values})
    raw['paragraphs'][0].update(formula='原文公式',table={'headers':['項目','內容'],'rows':[['甲','原文甲'],['乙','原文乙']]})
    p=nc.validate(raw,values,'full')[0]
    seen=[]
    def model(request):
        seen.append(request)
        return response(request)
    assert nc.review_paragraph(p,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')==[]
    assert seen[0]['parts']==['heading','text','formula','table.headers','table.row.0','table.row.1']
    assert seen[0]['claims']==[{'ref':'C1','text':'第 0 個重點','evidence_ids':['E1']}]
    assert [q for e in seen[0]['evidence'] for q in e['quotes']]==['原文 0']
    assert seen[0]['paragraph']['formula']=='原文公式'
    assert seen[0]['paragraph']['table']==raw['paragraphs'][0]['table']


def test_coverage_uses_already_checked_related_paragraphs_without_extra_review_calls():
    values=concepts();values[0]['claims']=values[0]['claims'][:1]
    seen=[]
    def model(request):
        if request.get('phase')=='review_paragraph':
            seen.append(request)
            verdict=response(request)
            for item in verdict['coverage']:
                item['note_parts']=['related.0.text','text']
            return verdict
        first=response(request)['paragraphs'][0]
        return {'paragraphs':[{**first,'text':'條件說明。','table':{'headers':['條件'],'rows':[['示例']]}},
            {**first,'text':'結論說明。'}]}
    result=full(values,model)
    assert len(result)==len(seen)==2
    assert seen[0]['coverage_claims']==[]
    assert seen[1]['coverage_claims']==['C1']
    assert seen[1]['related_paragraphs']==[{'claims':['C1'],'text':'條件說明。','formula':None,
        'table':{'headers':['條件'],'rows':[['示例']]}}]


@pytest.mark.parametrize('failure',['missing','duplicate','source_location','missing_location','empty_location','header_only','string_boolean'])
def test_coverage_requires_each_claim_and_actual_note_locations(failure):
    values=concepts();raw=response({'concepts':values})
    raw['paragraphs'][0]['table']={'headers':['名稱'],'rows':[['值']]}
    p=nc.validate(raw,values,'full')[0]
    def model(request):
        verdict=response(request)
        if failure=='missing':verdict['coverage']=[]
        if failure=='duplicate':verdict['coverage']*=2
        if failure=='source_location':verdict['coverage'][0]['note_parts']=['E1']
        if failure=='missing_location':verdict['coverage'][0]['note_parts']=['related.99.text']
        if failure=='empty_location':verdict['coverage'][0]['note_parts']=[]
        if failure=='header_only':verdict['coverage'][0]['note_parts']=['table.headers']
        if failure=='string_boolean':verdict['coverage'][0]['explained']='true'
        return verdict
    with pytest.raises(nc.ReviewInvalid):
        nc.review_paragraph(p,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')


def test_repair_does_not_rewrite_or_recheck_other_supported_paragraphs():
    calls=[]
    writers=[]
    seen_bad=False
    def model(request):
        nonlocal seen_bad
        calls.append(request)
        if request.get('phase')=='review_paragraph':
            rejected=request['paragraph']['text']=='第 1 個重點' and not seen_bad
            if rejected:seen_bad=True
            return {**response(request),'checks':[{'part':p,'supported':not (rejected and p=='text'),
                'reason':'需修正此段說明。' if rejected and p=='text' else ''} for p in request['parts']]}
        writers.append(request)
        return response(request)
    result=full(concepts(),model)
    assert len(writers)==2
    assert len(writers[1]['concepts'][0]['claims'])==1
    assert writers[1]['concepts'][0]['claims'][0]['text']=='第 1 個重點'
    reviewed=[r['paragraph']['text'] for r in calls if r.get('phase')=='review_paragraph']
    assert reviewed==['第 0 個重點','第 1 個重點','第 1 個重點','第 2 個重點']
    assert [p['text'] for p in result]==['第 0 個重點','第 1 個重點','第 2 個重點']


def test_writer_and_checker_keep_source_page_block_order_and_geometry():
    values=concepts()
    e=values[0]['claims'][0]['evidence'][0]
    e.update(source_id='source-a',source_name='A.pdf',page=7,normalized_page=2,block_order=4,
        source_locator={'page':7,'region':[12.3456,24.5678,102.7890,48.9012]})
    original=deepcopy(e)
    request,_=mn.model_request(values,'full')
    source=request['evidence'][0]
    assert source['source_id']=='source-a' and source['page']==2 and source['block_order']==4
    assert source['region']==[12.35,24.57,102.79,48.9]
    paragraph=nc.validate(response({'concepts':values}),values,'full')[0]
    checks=[]
    def model(request):
        checks.append(request)
        return response(request)
    nc.review_paragraph(paragraph,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')
    for key in ('source_id','source_name','page','block_order','region','quotes'):
        assert checks[0]['evidence'][0][key]==source[key]
    assert e==original and paragraph['evidence'][0]==original


def test_reference_repair_receives_exact_missing_and_extra_compact_ids():
    writers=[]
    def model(request):
        if request.get('phase')=='review_paragraph':return response(request)
        writers.append(request)
        result=response(request)
        if len(writers)==1:
            result['paragraphs'][0]['evidence_ids']=result['paragraphs'][1]['evidence_ids'][:]
            result['paragraphs'][2]['evidence_ids']=result['paragraphs'][1]['evidence_ids'][:]
        else:
            result['paragraphs'][0]['text']='不得採用的擴寫。'
            result['paragraphs'][1]['evidence_ids']=['unknown']
        return result
    result=mn.generate_part(concepts(),'full',model)
    assert result[0]['text']!='不得採用的擴寫。'
    assert len(writers)==3 and writers[2]['correction']['reference_error']['paragraph_index']==2
    assert writers[1]['correction']['reference_error']=={
        'paragraph_index':0,'claims_missing_evidence':['C1'],
        'evidence_options':{'C1':['E1']},
        'evidence_not_in_paragraph_claims':['E2']}


def test_reference_repair_missing_target_paragraph_has_explicit_error():
    writers=[]
    def model(request):
        writers.append(request)
        result=response(request)
        if len(writers)==1:
            result['paragraphs'][2]['evidence_ids']=result['paragraphs'][0]['evidence_ids'][:]
        else:
            result['paragraphs']=result['paragraphs'][:1]
        return result
    with pytest.raises(ValueError,match='^NOTE_FORMAT_INVALID$'):
        mn.generate_part(concepts(),'full',model)
    assert len(writers)==2


def reviewer(unexplained=(), unsupported=(), reviews=None):
    """依重點文字判定涵蓋、依正文判定事實；記錄實際送出的核對請求。"""
    def review(request):
        if reviews is not None:reviews.append(request)
        verdict=response(request)
        texts={c['ref']:c['text'] for c in request['claims']}
        for item in verdict['coverage']:
            if texts[item['claim']] in unexplained:item.update(explained=False,note_parts=[],reason='正文沒有說明這個重點。')
        if request['paragraph']['text'] in unsupported:
            verdict['checks']=[{'part':p,'supported':p!='text','reason':'來源沒有此條件。' if p=='text' else ''} for p in request['parts']]
        return verdict
    return review


def summary_model(draft, unexplained=(), unsupported=(), repair=None):
    """draft 以原始重點編號描述段落，寫作請求依本次短編號轉換。"""
    writers,reviews=[],[]
    review=reviewer(unexplained,unsupported,reviews)
    def model(request):
        if request.get('phase')=='review_paragraph':return review(request)
        writers.append(request)
        claims={q['text']:q for c in request['concepts'] for q in c['claims']}
        if request.get('correction',{}).get('problem')=='NOTE_CONTENT_UNSUPPORTED':
            return repair(request) if repair else response(request)
        return {'paragraphs':[{'heading':heading,'text':text,'table':None,'formula':None,
            'claim_ids':[claims[f'第 {i} 個重點']['claim_id'] for i in ids],
            'evidence_ids':[e for i in ids for e in claims[f'第 {i} 個重點']['evidence_ids']]}
            for heading,text,ids in draft]}
    return model,writers,reviews


def four_claims():
    values=concepts()
    values[0]['claims'].append({'claim_id':'q3','text':'第 3 個重點','evidence':[{'evidence_id':'e3','quote':'原文 3','page':4}]})
    return values


def test_summary_removes_unexplained_claims_from_several_paragraphs_and_rechecks_only_changed_ones():
    values=four_claims()
    draft=[('甲','第一段正文。',[0,1]),('乙','第二段正文。',[2,3]),('丙','第三段正文。',[1])]
    model,writers,reviews=summary_model(draft,unexplained={'第 1 個重點','第 3 個重點'})
    diagnostics=[]
    result=mn.generate_part(values,'summary',model,diagnostics)
    assert [p['claim_ids'] for p in result]==[['q0'],['q2']]
    assert [[e['evidence_id'] for e in p['evidence']] for p in result]==[['e0'],['e2']]
    # 第三段只有被移除的重點而整段刪除；只重核引用改變的段落，未變的沿用結果。
    assert [r['paragraph']['text'] for r in reviews]==['第一段正文。','第二段正文。','第二段正文。','第三段正文。','第一段正文。']
    assert len(writers)==1
    removed=[d for d in diagnostics if d['event']=='claims_removed']
    assert [set(d['claims']) for d in removed]==[{'q3'},{'q1'}]
    assert {'event':'paragraph_dropped','reason':'no_claims','claims':['q1']} in diagnostics
    rejected=next(d for d in diagnostics if d['event']=='review_rejected')
    assert rejected['issues'][0]['part']=='claim.C2' and len(rejected['issues'][0]['reason_sha256'])==12
    assert '正文沒有說明' not in str(diagnostics)


def test_summary_keeps_sources_shared_with_remaining_claims():
    values=concepts()
    values[0]['claims'][1]['evidence'].append(deepcopy(values[0]['claims'][0]['evidence'][0]))
    model,_,_=summary_model([('甲','正文。',[0,1])],unexplained={'第 1 個重點'})
    result=mn.generate_part(values,'summary',model)
    assert result[0]['claim_ids']==['q0'] and [e['evidence_id'] for e in result[0]['evidence']]==['e0']


def test_summary_fails_when_a_concept_is_still_unexplained_after_one_local_cover():
    values=concepts()
    model,writers,_=summary_model([('甲','正文。',[0]),('乙','正文二。',[1])],unexplained={'第 0 個重點','第 1 個重點'})
    diagnostics=[]
    with pytest.raises(ValueError,match='^NOTE_COVERAGE_INCOMPLETE$'):
        mn.generate_part(values,'summary',model,diagnostics)
    # 全稿刪光表示概念沒有被解說：只補寫一次，補不出來就失敗，不發布空稿。
    assert len(writers)==2 and writers[1]['correction']['problem']=='NOTE_CONCEPT_UNEXPLAINED'
    assert [d['after_repair'] for d in diagnostics if d['event']=='concepts_unexplained']==[False,True]


def test_summary_removes_unexplained_claim_before_local_fact_repair():
    values=concepts()
    def repair(request):
        assert [q['text'] for c in request['concepts'] for q in c['claims']]==['第 0 個重點']
        return {'paragraphs':[{**response(request)['paragraphs'][0],'text':'修正後正文。'}]}
    model,writers,reviews=summary_model([('甲','有誤正文。',[0,1]),('乙','第二段。',[2])],
        unexplained={'第 1 個重點'},unsupported={'有誤正文。'},repair=repair)
    result=mn.generate_part(values,'summary',model)
    assert [p['text'] for p in result]==['修正後正文。','第二段。']
    assert [p['claim_ids'] for p in result]==[['q0'],['q2']]
    fixes=[w for w in writers if w.get('correction',{}).get('problem')=='NOTE_CONTENT_UNSUPPORTED']
    assert len(fixes)==1
    assert [r['paragraph']['text'] for r in reviews]==['有誤正文。','有誤正文。','修正後正文。','第二段。']


def test_summary_drops_paragraph_still_unsupported_after_one_repair_and_publishes_the_rest():
    values=concepts()
    def repair(request):
        return {'paragraphs':[{**response(request)['paragraphs'][0],'text':'仍有誤正文。'}]}
    model,writers,_=summary_model([('甲','第一段。',[0]),('乙','有誤正文。',[1]),('丙','第三段。',[2])],
        unsupported={'有誤正文。','仍有誤正文。'},repair=repair)
    diagnostics=[]
    result=mn.generate_part(values,'summary',model,diagnostics)
    assert [p['text'] for p in result]==['第一段。','第三段。']
    assert [p['id'] for p in result]==['0','1']
    assert len([w for w in writers if w.get('correction',{}).get('problem')=='NOTE_CONTENT_UNSUPPORTED'])==1
    assert {'event':'paragraph_dropped','reason':'content_unsupported','claims':['q1']} in diagnostics


def test_summary_references_are_normalized_by_program_then_fact_checked():
    values=concepts()
    writers,reviews=[],[]
    def model(request):
        if request.get('phase')=='review_paragraph':
            reviews.append(request)
            return response(request)
        writers.append(request)
        raw=response(request)
        # 第一段的 C2 沒引用自己的來源；第二段引用了不屬於本段重點的 E1 與不存在的 E9。
        raw['paragraphs'][0]['claim_ids'].append('C2')
        raw['paragraphs'][1]['evidence_ids']=[*raw['paragraphs'][1]['evidence_ids'],'E1','E9']
        return raw
    diagnostics=[]
    result=mn.generate_part(values,'summary',model,diagnostics)
    assert all('reference_error' not in w.get('correction',{}) for w in writers)
    assert [p['claim_ids'] for p in result]==[['q0'],['q1'],['q2']]
    assert [[e['evidence_id'] for e in p['evidence']] for p in result]==[['e0'],['e1'],['e2']]
    assert len(reviews)==3 and reviews[1]['claims']==[{'ref':'C1','text':'第 1 個重點','evidence_ids':['E1']}]
    trimmed=[d for d in diagnostics if d['event']=='references_trimmed']
    assert [(d['paragraph'],d['claims_removed'],d['evidence_removed']) for d in trimmed]==[(0,1,0),(1,0,2)]


def test_summary_local_repair_references_are_normalized_too():
    values=concepts()
    def repair(request):
        raw=response(request)
        raw['paragraphs'][0].update(text='修正後正文。',evidence_ids=[*raw['paragraphs'][0]['evidence_ids'],'E7'])
        return raw
    model,writers,_=summary_model([('甲','有誤正文。',[0]),('乙','第二段。',[1])],unsupported={'有誤正文。'},repair=repair)
    result=mn.generate_part(values,'summary',model)
    assert [p['text'] for p in result]==['修正後正文。','第二段。']
    assert [e['evidence_id'] for e in result[0]['evidence']]==['e0']


def test_full_local_repair_gets_reference_correction_and_still_requires_every_claim():
    values=concepts()
    writers=[]
    rejected=[]
    def model(request):
        if request.get('phase')=='review_paragraph':
            verdict=response(request)
            if not rejected:
                rejected.append(request)
                verdict['checks']=[{'part':p,'supported':p!='text','reason':'需修正。' if p=='text' else ''} for p in request['parts']]
            return verdict
        writers.append(request)
        joined={'heading':'合併','text':'合併正文。','table':None,'formula':None}
        correction=request.get('correction',{})
        if not correction:
            return {'paragraphs':[{**joined,'claim_ids':['C1','C2'],'evidence_ids':['E1','E2']},
                {**joined,'text':'第三點。','claim_ids':['C3'],'evidence_ids':['E3']}]}
        if 'reference_error' in correction:
            return {'paragraphs':[{**joined,'text':'不得採用的改寫。','claim_ids':['C1','C2'],'evidence_ids':['E1','E2']}]}
        # 局部修正回應漏列 C2 的來源。
        return {'paragraphs':[{**joined,'text':'修正正文。','claim_ids':['C1','C2'],'evidence_ids':['E1']}]}
    result=full(values,model)
    assert len(writers)==3
    assert writers[2]['correction']['problem']=='NOTE_REFERENCE_INVALID'
    assert writers[2]['correction']['reference_error']['claims_missing_evidence']==['C2']
    assert [p['text'] for p in result]==['修正正文。','第三點。']
    assert [p['claim_ids'] for p in result]==[['q0','q1'],['q2']]


def test_full_notes_never_drop_unexplained_claims():
    values=concepts()
    review=reviewer(unexplained={'第 1 個重點'})
    with pytest.raises(ValueError,match='^NOTE_CONTENT_UNSUPPORTED$'):
        full(values,lambda r:review(r) if r.get('phase')=='review_paragraph' else response(r))
    with pytest.raises(ValueError,match='NOTE_COVERAGE_INCOMPLETE'):
        nc.validate(response({'concepts':[{**values[0],'claims':values[0]['claims'][:2]}]}),values,'full')


def three_concepts():
    return [{"concept_id": f"k{i}", "label": f"概念{i}", "claims": [
        {"claim_id": f"q{i}{j}", "text": f"概念{i}的第 {j} 點", "evidence": [{"evidence_id": f"e{i}{j}", "quote": f"原文 {i}{j}", "page": i + 1}]}
        for j in range(2)]} for i in range(3)]


def summary(values, call, budget, diagnostics=None):
    plan = mn.summary_plan(values, call, input_budget=budget)
    return mn.complete_sections(values, plan, call, [], lambda *args: None, diagnostics, "summary")


def test_summary_covers_every_concept_by_locally_writing_the_missing_one_in_learning_order():
    values=three_concepts()
    writers,reviews=[],[]
    def model(request):
        if request.get('phase')=='review_paragraph':
            reviews.append(request)
            return response(request)
        writers.append(request)
        result=response(request)
        if 'correction' not in request:
            # 首稿漏寫概念1，其餘每個概念只寫第一點。
            result['paragraphs']=[p for p in result['paragraphs'] if p['claim_ids'] in (['C1'],['C5'])]
        return result
    diagnostics=[]
    result=summary(values,model,10**6,diagnostics)
    assert len(writers)==2 and writers[1]['correction']['problem']=='NOTE_CONCEPT_UNEXPLAINED'
    assert [c['label'] for c in writers[1]['concepts']]==['概念1']
    assert [p['claim_ids'] for p in result]==[['q00'],['q10'],['q11'],['q20']]
    assert [p['id'] for p in result]==['0','1','2','3']
    assert {'event':'concepts_unexplained','concepts':['k1'],'after_repair':False} in diagnostics
    assert len(reviews)==4


def test_summary_without_selection_sends_every_point_and_has_no_page_limit():
    values=three_concepts()
    for c in values:
        for q in c['claims']:
            q['evidence'][0]['quote']='保留完整條件與數字，不能裁切來源。'*300
    calls=[]
    def model(request):
        calls.append(request)
        return response(request)
    request,_=mn.model_request(values,'summary')
    size=mn.estimate_prompt_tokens(nc.PROMPT,request)
    result=summary(values,model,size)
    assert all(r.get('phase')!='select_summary' for r in calls)
    assert len([r for r in calls if r.get('phase') is None])==1
    assert len(result)==6
    assert calls[0]['evidence'][0]['quotes']==[values[0]['claims'][0]['evidence'][0]['quote']]


def test_large_summary_selects_core_points_per_concept_without_counting_page_context():
    values=three_concepts()
    values[0]['claims']=values[0]['claims'][:1]  # 只有一個候選的概念不必請模型挑。
    for c in values:
        for q in c['claims']:
            q['evidence'][0]['source_name']=f"{c['label']}.pdf"
            q['context_evidence']=[{**q['evidence'][0],'evidence_id':'ctx'+q['claim_id'],'quote':'同頁補充原文。'*400}]
    calls=[]
    def model(request):
        calls.append(request)
        if request.get('phase')=='select_summary':
            return {'concepts':[{'concept':k['ref'],'claim_ids':[k['claims'][-1]['ref']]} for k in request['concepts']]}
        return response(request)
    budget=mn.estimate_prompt_tokens(nc.PROMPT,mn.model_request(values,'summary')[0])-1
    result=summary(values,model,budget)
    selections=[r for r in calls if r.get('phase')=='select_summary']
    assert len(selections)==1
    assert [k['label'] for k in selections[0]['concepts']]==['概念1','概念2']
    assert selections[0]['concepts'][0]['claims'][0]=={'ref':'C1','text':'概念1的第 0 點','sources':['概念1.pdf']}
    assert '同頁補充原文' not in str(selections[0])
    assert {q for p in result for q in p['claim_ids']}=={'q00','q11','q21'}
    writer=next(r for r in calls if r.get('phase') is None)
    assert any('同頁補充原文' in q for e in writer['evidence'] for q in e['quotes'])


def test_selection_batches_offer_each_point_once_within_budget():
    values=three_concepts()
    for c in values:
        c['claims'].append({'claim_id':c['concept_id']+'x','text':c['label']+'的補充點','evidence':[
            {'evidence_id':'e'+c['concept_id']+'x','quote':'原文','page':9}]})
    budget=mn.estimate_prompt_tokens(nc.SELECTION_PROMPT,mn._selection_request([(values[0],values[0]['claims'])])[0])+20
    calls=[]
    def model(request):
        calls.append(request)
        return {'concepts':[{'concept':k['ref'],'claim_ids':[k['claims'][0]['ref']]} for k in request['concepts']]}
    selected=mn.select_summary(values,model,input_budget=budget)
    assert len(calls)==3
    sent=[q['text'] for r in calls for k in r['concepts'] for q in k['claims']]
    assert len(sent)==len(set(sent))==9
    assert all(mn.estimate_prompt_tokens(nc.SELECTION_PROMPT,r)<=budget for r in calls)
    assert [[q['claim_id'] for q in c['claims']] for c in selected]==[['q00'],['q10'],['q20']]


def test_selected_summary_too_large_for_one_request_is_written_in_parts_like_full_notes():
    values=three_concepts()
    for c in values:
        for q in c['claims']:
            q['evidence'][0]['quote']='較長原文，寫作時完整帶入。'*80
    unit={**values[0],'claims':values[0]['claims'][:1]}
    budget=mn.estimate_prompt_tokens(nc.PROMPT,mn.model_request([unit],'summary')[0])+50
    calls=[]
    def model(request):
        calls.append(request)
        if request.get('phase')=='select_summary':
            return {'concepts':[{'concept':k['ref'],'claim_ids':[k['claims'][0]['ref']]} for k in request['concepts']]}
        return response(request)
    plan=mn.summary_plan(values,model,input_budget=budget)
    assert len([r for r in calls if r.get('phase')=='select_summary'])==1
    parts=[part for section in plan for part in section['parts']]
    assert [[c['claim_ids'] for c in part] for part in parts]==[[['q00']],[['q10']],[['q20']]]
    result=mn.complete_sections(values,plan,model,[],lambda *args:None,None,'summary')
    assert [p['claim_ids'] for p in result]==[['q00'],['q10'],['q20']]
    writers=[r for r in calls if r.get('phase') is None]
    assert len(writers)==3 and all(e['quotes']==['較長原文，寫作時完整帶入。'*80] for w in writers for e in w['evidence'])


def test_shared_points_are_offered_once_and_every_concept_still_gets_a_point():
    values=three_concepts()
    shared=deepcopy(values[0]['claims'][1])
    values[1]['claims']=[shared]
    values[2]['claims']=[deepcopy(values[0]['claims'][1]),deepcopy(values[0]['claims'][0])]
    calls=[]
    def model(request):
        calls.append(request)
        return {'concepts':[{'concept':k['ref'],'claim_ids':[k['claims'][0]['ref']]} for k in request['concepts']]}
    selected=mn.select_summary(values,model,input_budget=10**6)
    assert len(calls)==1 and [k['label'] for k in calls[0]['concepts']]==['概念0']
    # 概念1、2 只有與概念0 共用的重點；概念1 的共用點沒被挑中時取它的第一點，概念2 因此也涵蓋到。
    assert [[q['claim_id'] for q in c['claims']] for c in selected]==[['q00'],['q01'],['q01','q00']]


@pytest.mark.parametrize('bad',['missing_concept','foreign_claim','empty'])
def test_summary_selection_rejects_invalid_picks_after_one_correction(bad):
    values=three_concepts()
    calls=[]
    def model(request):
        calls.append(request)
        picks=[{'concept':k['ref'],'claim_ids':[k['claims'][0]['ref']]} for k in request['concepts']]
        if bad=='missing_concept':picks.pop()
        if bad=='foreign_claim':picks[0]['claim_ids']=[request['concepts'][1]['claims'][0]['ref']]
        if bad=='empty':picks[0]['claim_ids']=[]
        return {'concepts':picks}
    with pytest.raises(ValueError,match='^NOTE_SELECTION_INVALID$'):
        mn.select_summary(values,model,input_budget=10**6)
    assert len(calls)==2 and 'correction' in calls[1]


def test_point_too_large_only_because_of_page_context_keeps_its_own_evidence():
    values=three_concepts()
    big=values[1]['claims'][0]
    big['context_evidence']=[{**big['evidence'][0],'evidence_id':f'ctx{i}','quote':f'同頁其他區塊 {i}。'*40} for i in range(60)]
    small=values[2]['claims'][0]
    small['context_evidence']=[{**small['evidence'][0],'evidence_id':'header','quote':'欄名'}]
    budget=mn.estimate_prompt_tokens(nc.PROMPT,mn.model_request([{**values[1],'claims':[big]}],'summary')[0])-1
    diagnostics=[]
    prepared=mn.fit_page_context(values,'summary',budget,diagnostics)
    assert prepared[1]['claims'][0]['context_evidence']==[] and prepared[1]['claims'][0]['evidence']==big['evidence']
    assert prepared[2]['claims'][0]['context_evidence']==small['context_evidence']
    assert len(values[1]['claims'][0]['context_evidence'])==60
    assert diagnostics==[{'event':'context_omitted','claims':['q10']}]
    result=summary(prepared,response,budget)
    assert {q for p in result for q in p['claim_ids']}>={'q00','q10','q20'}


def test_failed_part_is_retried_up_to_three_attempts_without_rerunning_passed_parts():
    values=three_concepts()
    for c in values:c['claims']=c['claims'][:1]
    plan=[{'label':'區域','parts':[[{'concept_id':c['concept_id'],'claim_ids':[c['claims'][0]['claim_id']]}] for c in values]}]
    writers=[]
    def model(request,fail_times):
        if request.get('phase')=='review_paragraph':
            first=request['claims'][0]['text']
            rejected=first=='概念1的第 0 點' and sum(1 for w in writers if w['concepts'][0]['label']=='概念1')<=fail_times
            return {**response(request),'checks':[{'part':p,'supported':not (rejected and p=='text'),
                'reason':'來源沒有此條件。' if rejected and p=='text' else ''} for p in request['parts']]}
        writers.append(request)
        return response(request)
    saved=[]
    diagnostics=[]
    # 第二批的前兩次嘗試（各含局部重寫與概念補寫）都被擋，第三次通過；其他批次只寫一次。
    result=mn.complete_sections(values,plan,lambda r:model(r,6),[],lambda i,p:saved.append(i),diagnostics,'summary')
    assert [p['claim_ids'] for p in result]==[['q00'],['q10'],['q20']] and saved==[0,1,2]
    assert [w['concepts'][0]['label'] for w in writers if 'correction' not in w]==['概念0','概念1','概念1','概念1','概念2']
    assert {'event':'part_attempts','part':1,'attempts':3,'failed':['NOTE_COVERAGE_INCOMPLETE']*2,'succeeded':True} in diagnostics
    writers.clear();saved.clear();diagnostics.clear()
    with pytest.raises(ValueError,match='^NOTE_CONTENT_UNSUPPORTED$'):
        mn.complete_sections(values,plan,lambda r:model(r,99),[],lambda i,p:saved.append(i),diagnostics,'full')
    assert saved==[0] and len([w for w in writers if 'correction' not in w])==1+3
    assert diagnostics[-1]=={'event':'part_attempts','part':1,'attempts':3,'failed':['NOTE_CONTENT_UNSUPPORTED']*3,'succeeded':False}


@pytest.mark.parametrize('error',[ValueError('NOTE_INPUT_TOO_LARGE'),ValueError('NOTE_CONTRACT_INVALID'),RuntimeError('SEMANTIC_SERVICE_CONFIG_INVALID')])
def test_errors_that_retry_cannot_fix_are_not_retried(error):
    values=three_concepts()
    plan=mn.plan_sections(values,[{'label':'區域','concept_ids':[c['concept_id'] for c in values]}])
    calls=[]
    def model(request):
        calls.append(request)
        raise error
    with pytest.raises(type(error),match=str(error)):
        mn.complete_sections(values,plan,model,[],lambda *args:None,None,'full')
    assert len(calls)==1


def test_full_notes_omit_only_oversized_page_context_and_still_fail_when_own_evidence_is_too_large(monkeypatch):
    values=three_concepts()
    big=values[1]['claims'][0]
    big['context_evidence']=[{**big['evidence'][0],'evidence_id':f'ctx{i}','quote':f'同頁其他區塊 {i}。'*40} for i in range(60)]
    small=values[2]['claims'][0]
    small['context_evidence']=[{**small['evidence'][0],'evidence_id':'header','quote':'欄名'}]
    monkeypatch.setattr(mn,'BATCH_TOKENS',mn.estimate_prompt_tokens(nc.PROMPT,mn.model_request([{**values[1],'claims':[big]}],'full')[0])-1)
    diagnostics=[]
    prepared=mn.fit_page_context(values,'full',mn.BATCH_TOKENS,diagnostics)
    assert prepared[1]['claims'][0]['context_evidence']==[] and prepared[1]['claims'][0]['evidence']==big['evidence']
    assert prepared[2]['claims'][0]['context_evidence']==small['context_evidence']
    assert diagnostics==[{'event':'context_omitted','claims':['q10']}]
    result=full(prepared,response)
    assert {q for p in result for q in p['claim_ids']}=={q['claim_id'] for c in values for q in c['claims']}
    with pytest.raises(ValueError,match='NOTE_INPUT_TOO_LARGE'):
        mn.plan_sections(values,[{'label':'區域','concept_ids':[c['concept_id'] for c in values]}])
    huge=deepcopy(prepared)
    huge[0]['claims'][0]['evidence'][0]['quote']='自身原文很長。'*12000
    assert mn.fit_page_context(huge,'full',mn.BATCH_TOKENS,[])[0]['claims'][0]['evidence']==huge[0]['claims'][0]['evidence']
    with pytest.raises(ValueError,match='NOTE_INPUT_TOO_LARGE'):
        mn.plan_sections(mn.fit_page_context(huge,'full',mn.BATCH_TOKENS,[]),[{'label':'區域','concept_ids':[c['concept_id'] for c in huge]}])

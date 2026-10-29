"""來源核對、完整筆記有限補寫、章級速覽與續接的回歸。"""
from copy import deepcopy
import pytest
from runtime import note_content as nc, material_notes as mn



def concepts():
    return [{"concept_id": "c1", "label": "訊號", "claims": [
        {"claim_id": f"q{i}", "text": f"第 {i} 個重點", "evidence": [{"evidence_id": f"e{i}", "quote": f"原文 {i}", "page": i + 1}]}
        for i in range(3)]}]



def para(heading, text, claim_ids, evidence_ids, *, formula=None, table=None, level=1):
    """測試用小節：一個文字區塊，可再接公式與表格（位置依序為 blocks.0／blocks.1…）。"""
    blocks = [{"type": "text", "text": text}]
    if formula:
        blocks.append({"type": "formula", "latex": formula})
    if table:
        blocks.append({"type": "table", "caption": None, **table})
    return {"heading": heading, "level": level, "blocks": blocks, "claim_ids": claim_ids, "evidence_ids": evidence_ids}



def text_of(paragraph):
    """小節或核對請求中第一個文字區塊的內容。"""
    if "content" in paragraph:
        return paragraph["content"][0]["text"]
    return paragraph["blocks"][0]["text"]



def with_text(paragraph, text):
    return {**paragraph, "blocks": [{"type": "text", "text": text}, *paragraph["blocks"][1:]]}


def response(request):
    if request.get("phase") == "select_summary":
        chosen = {q["ref"] for q in [q for c in request["concepts"] for q in c["claims"]][:5]}
        return {"concepts": [{"concept": c["ref"], "claim_ids": [q["ref"] for q in c["claims"] if q["ref"] in chosen]}
            for c in request["concepts"]]}
    if request.get("phase") == "review_paragraph":
        return {"checks": [{"part": part, "missing_evidence": [], "supported": True, "reason": ""} for part in request["parts"]],
            "coverage": [{"claim": ref, "explained": True,
                "note_parts": [next(p for p in request["coverage_locations"][ref]
                    if not p.endswith((".headers", ".caption", ".callout")))], "reason": ""}
                for ref in request["coverage_claims"]]}
    return {"paragraphs": [para(c["label"], q["text"], [q["claim_id"]],
        q.get("evidence_ids") or [e["evidence_id"] for e in q["evidence"]])
        for c in request["concepts"] for q in c["claims"]]}


def sections(values):
    return [{"section_id": "s1", "heading": "原教材章", "level": 1, "parent_id": None,
        "claim_ids": list(dict.fromkeys(q["claim_id"] for c in values for q in c["claims"]))}]


def full(values, call):
    return mn.complete_sections(values, mn.plan_sections(values, sections(values)), call, [], lambda *args: None)


def summary(values, call, budget, diagnostics=None):
    plan = mn.summary_plan(values, sections(values), call, input_budget=budget)
    return mn.complete_sections(values, plan, call, [], lambda *args: None, diagnostics, "summary")


def check_written_paragraphs(values, kind, model, diagnostics=None):
    """合法結構稿直接進共用核對邊界，逐項測原始位置的刪除與來源保存。"""
    request, identities = mn.model_request(values, kind)
    draft = nc.validate(mn.canonical_response(model(request), identities), values, kind)
    return mn._check_paragraphs(draft, values, model, [] if diagnostics is None else diagnostics, kind=kind)



def test_complete_notes_preserve_all_claims_and_source_pages():
    result = full(concepts(), response)
    assert [p["id"] for p in result] == ["0", "1", "2"]
    assert {x for p in result for x in p["claim_ids"]} == {"q0", "q1", "q2"}
    assert [p["evidence"][0]["page"] for p in result] == [1, 2, 3]


def test_summary_caps_actual_core_points_before_the_single_review():
    reviews = []
    def model(request):
        if request.get('phase') == 'review_paragraph':
            reviews.append(request)
            assert len([p for p in request['paragraph']['content'] if p['type'] == 'bullet']) == 1
            return response(request)
        value = response(request)
        if 'paragraphs' in value:
            for paragraph in value['paragraphs']:
                paragraph['blocks'] = [
                    {'type': 'bullets', 'items': [{'text': f'核心{i}', 'children': [
                        {'text': '延伸細節', 'children': []}]} for i in range(4)]},
                    {'type': 'table', 'caption': None, 'headers': ['欄'], 'rows': [['細節']]},
                ]
        return value
    result = summary(concepts(), model, 50000)
    assert len(reviews) == 3
    assert sum(len(block['items']) for p in result for block in p['blocks']) == 3
    assert all(block['type'] == 'bullets' and block['items'][0]['children'] == []
        for p in result for block in p['blocks'])



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



@pytest.mark.parametrize('failure',['unknown','duplicate','supported'])
def test_review_rejects_invalid_missing_reference_selection(failure):
    values=concepts()
    claim=values[0]['claims'][0]
    claim['context_evidence']=[{**claim['evidence'][0],'evidence_id':'header','quote':'表頭'}]
    paragraph=nc.validate(response({'concepts':values}),values,'full')[0]
    def model(request):
        result=response(request)
        result['checks'][1].update(supported=failure=='supported',reason='缺引用',
            missing_evidence=['X999'] if failure=='unknown' else ['X1','X1'] if failure=='duplicate' else ['X1'])
        return result
    with pytest.raises(nc.ReviewInvalid):
        nc.review_paragraph(paragraph,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')



@pytest.mark.parametrize("failure", ["foreign_claim", "foreign_evidence", "wrong_claim_evidence", "ragged_table"])
def test_invalid_model_output_is_not_published(failure):
    value = response({"concepts": concepts()})
    if failure == "foreign_claim": value["paragraphs"][0]["claim_ids"] = ["foreign"]
    if failure == "foreign_evidence": value["paragraphs"][0]["evidence_ids"] = ["foreign"]
    if failure == "wrong_claim_evidence": value["paragraphs"][0]["evidence_ids"] = ["e1"]
    if failure == "ragged_table": value["paragraphs"][0]["blocks"].append({"type": "table", "caption": None, "headers": ["a", "b"], "rows": [["only one"]]})
    with pytest.raises(ValueError): nc.validate(value, concepts(), "full")



def test_regeneration_matches_only_identical_points_and_sources_and_never_duplicates_annotation():
    original = full(concepts(), response)
    annotations = [{"anchor": original[0]["anchor"], "body": "我的想法", "original_paragraph": deepcopy(original[0])}]
    new = deepcopy(original)
    new[0] = with_text(new[0], "重新組織的說明")
    assert nc.annotation_matches(new, annotations, None, None)[0]["paragraph_id"] == "0"
    changed = concepts()
    changed[0]["claims"][0]["text"] += "新增條件"
    replaced = full(changed, response)
    orphaned = nc.annotation_matches(replaced, annotations, None, None)[0]
    assert orphaned["paragraph_id"] is None
    assert orphaned["body"] == "我的想法" and orphaned["original_paragraph"] == original[0]
    assert nc.annotation_matches([*original, original[0]], annotations, None, None)[0]["paragraph_id"] is None



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
    plan = mn.plan_sections(values, sections(values), kind)
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
    {"version": "material-notes/v999"},
])
def test_invalid_historical_notes_contract_is_rejected(change):
    from runtime.local_ai_config import configured_lock
    from runtime.material_runtime import validate_runtime_lock
    from runtime.material_runtime import MaterialAnalysisError
    lock = deepcopy(configured_lock())
    lock["material_notes"].update(change)
    with pytest.raises(MaterialAnalysisError, match="RUNTIME_LOCK_INVALID"):
        validate_runtime_lock(lock)



@pytest.mark.parametrize("version", ["material-notes/v10", "material-notes/v11", "material-notes/v12"])
def test_notes_generation_rejects_historical_policy(monkeypatch, version):
    lock = deepcopy(mn.configured_lock())
    lock["material_notes"]["version"] = version
    if version == "material-notes/v10":
        lock["material_notes"]["summary_layout"] = "a4-12pt-18mm/v1"
    if version == "material-notes/v12":
        lock["material_notes"]["review_prompt"] = "歷史 v12 核對提示"
    claim = {"lock": lock, "key": (None, None, None, "summary"), "token": "lease"}
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
    raw={'paragraphs':[para('共同來源','兩個重點各有原文依據。',['q0','q1'],['shared'])]}
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



@pytest.mark.parametrize('bad_response', ['missing_part','duplicate_part','string_boolean','empty_checks'])
def test_content_review_rejects_missing_or_malformed_verdicts(bad_response):
    raw=response({'concepts':concepts()})
    paragraph=nc.validate(raw,concepts(),'full')[0]
    def model(request):
        checks=[{'part':p,'missing_evidence': [], 'supported':True,'reason':''} for p in request['parts']]
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
    first=raw['paragraphs'][0]
    raw['paragraphs'][0]=para(first['heading'],text_of(first),first['claim_ids'],first['evidence_ids'],
        formula=r'y=\text{原文公式}',table={'headers':['項目','內容'],'rows':[['甲','原文甲'],['乙','原文乙']]})
    p=nc.validate(raw,values,'full')[0]
    seen=[]
    def model(request):
        seen.append(request)
        return response(request)
    assert nc.review_paragraph(p,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')==[]
    assert seen[0]['parts']==['heading','blocks.0','blocks.1.formula','blocks.2.headers','blocks.2.row.0','blocks.2.row.1']
    assert seen[0]['claims']==[{'ref':'C1','text':'第 0 個重點','evidence_ids':['E1']}]
    assert [q for e in seen[0]['evidence'] for q in e['quotes']]==['原文 0']
    content=seen[0]['paragraph']['content']
    assert content[1]=={'part':'blocks.1.formula','type':'formula','latex':r'y=\text{原文公式}'}
    assert [c.get('cells') for c in content[2:]]==[['項目','內容'],['甲','原文甲'],['乙','原文乙']]



@pytest.mark.parametrize('failure',['missing','duplicate','source_location','missing_location','empty_location','header_only','string_boolean'])
def test_coverage_requires_each_claim_and_actual_note_locations(failure):
    values=concepts();raw=response({'concepts':values})
    raw['paragraphs'][0]['blocks'].append({'type':'table','caption':None,'headers':['名稱'],'rows':[['值']]})
    p=nc.validate(raw,values,'full')[0]
    def model(request):
        verdict=response(request)
        if failure=='missing':verdict['coverage']=[]
        if failure=='duplicate':verdict['coverage']*=2
        if failure=='source_location':verdict['coverage'][0]['note_parts']=['E1']
        if failure=='missing_location':verdict['coverage'][0]['note_parts']=['related.99.blocks.0']
        if failure=='empty_location':verdict['coverage'][0]['note_parts']=[]
        if failure=='header_only':verdict['coverage'][0]['note_parts']=['blocks.1.headers']
        if failure=='string_boolean':verdict['coverage'][0]['explained']='true'
        return verdict
    with pytest.raises(nc.ReviewInvalid):
        nc.review_paragraph(p,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')



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
            result['paragraphs'][0]=with_text(result['paragraphs'][0],'不得採用的擴寫。')
            result['paragraphs'][1]['evidence_ids']=['unknown']
        return result
    result=mn.generate_part(concepts(),'full',model)
    assert text_of(result[0])!='不得採用的擴寫。'
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



def three_concepts():
    return [{"concept_id": f"k{i}", "label": f"概念{i}", "claims": [
        {"claim_id": f"q{i}{j}", "text": f"概念{i}的第 {j} 點", "evidence": [{"evidence_id": f"e{i}{j}", "quote": f"原文 {i}{j}", "page": i + 1}]}
        for j in range(2)]} for i in range(3)]



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
    assert 3 <= len({q for p in result for q in p['claim_ids']}) <= 5



@pytest.mark.parametrize('error',[ValueError('NOTE_INPUT_TOO_LARGE'),ValueError('NOTE_CONTRACT_INVALID'),RuntimeError('SEMANTIC_SERVICE_CONFIG_INVALID')])
def test_errors_that_retry_cannot_fix_are_not_retried(error):
    values=three_concepts()
    plan=mn.plan_sections(values,sections(values))
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
        mn.plan_sections(values,sections(values))
    huge=deepcopy(prepared)
    huge[0]['claims'][0]['evidence'][0]['quote']='自身原文很長。'*12000
    assert mn.fit_page_context(huge,'full',mn.BATCH_TOKENS,[])[0]['claims'][0]['evidence']==huge[0]['claims'][0]['evidence']
    with pytest.raises(ValueError,match='NOTE_INPUT_TOO_LARGE'):
        mn.plan_sections(mn.fit_page_context(huge,'full',mn.BATCH_TOKENS,[]),sections(huge))



def test_oversized_page_context_keeps_standalone_normative_words():
    values=concepts()
    values[0]['claims']=values[0]['claims'][:1]
    claim=values[0]['claims'][0]
    word={**claim['evidence'][0],'evidence_id':'norm','quote':'MUST'}
    claim['context_evidence']=[word,{**word,'evidence_id':'large','quote':'同頁其他內容。'*5000}]
    minimal=[{**values[0],'claims':[{**claim,'context_evidence':[word]}]}]
    budget=mn.estimate_prompt_tokens(nc.PROMPT,mn.model_request(minimal,'full')[0])+1
    prepared=mn.fit_page_context(values,'full',budget,[])
    assert prepared[0]['claims'][0]['context_evidence']==[word]
    assert mn.plan_sections(prepared,sections(prepared),budget=budget)
    assert claim['context_evidence'][1]['evidence_id']=='large'



def structured_paragraph():
    items=[{'text':'上層','children':[{'text':'子項','children':[{'text':'孫項'}]}]}]
    return {'heading':'結構','level':1,'claim_ids':['q0'],'evidence_ids':['e0'],'blocks':[
        {'type':'bullets','items':items},
        {'type':'table','caption':'比較表','headers':['甲','乙'],'rows':[['1','2']]},
        {'type':'formula','latex':r'a=\frac{b}{c}'},
        {'type':'callout','style':'case_study','title':'原文案例','items':[{'text':'案例內容','children':[]}]}]}



def test_every_bullet_row_formula_and_callout_is_a_located_review_part():
    values=concepts()
    p=nc.validate({'paragraphs':[structured_paragraph()]},values,'summary')[0]
    seen=[]
    def model(request):
        seen.append(request)
        verdict=response(request)
        verdict['coverage'][0]['note_parts']=['blocks.0.item.0.0','blocks.2.formula']
        return verdict
    assert nc.review_paragraph(p,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')==[]
    assert seen[0]['parts']==['heading','blocks.0.item.0','blocks.0.item.0.0','blocks.0.item.0.0.0',
        'blocks.1.caption','blocks.1.headers','blocks.1.row.0','blocks.2.formula','blocks.3.callout','blocks.3.item.0']
    assert seen[0]['paragraph']['content'][2]=={'part':'blocks.0.item.0.0.0','type':'bullet','depth':3,'text':'孫項'}
    assert seen[0]['paragraph']['content'][-1]=={'part':'blocks.3.item.0','type':'bullet','depth':1,'text':'案例內容','callout':'案例研究'}
    assert seen[0]['coverage_locations']=={'C1':seen[0]['parts'][1:]}



@pytest.mark.parametrize('label',['blocks.1.caption','blocks.3.callout'])
def test_table_caption_or_callout_label_alone_is_not_an_explanation(label):
    values=concepts()
    p=nc.validate({'paragraphs':[structured_paragraph()]},values,'summary')[0]
    def model(request):
        verdict=response(request)
        verdict['coverage'][0]['note_parts']=[label]
        return verdict
    with pytest.raises(nc.ReviewInvalid):
        nc.review_paragraph(p,model,claims={q['claim_id']:q for c in values for q in c['claims']},kind='full')



@pytest.mark.parametrize('latex',[r'\frac{a}{b', r'\foo{x}', '100%', r'\begin{matrix}a\end{matrix}', r'\left( x', 'a \\\\ b', r'x \le y', 'eps_hat', r'\left\mid x \right\mid', 'a \times b'])
def test_unparseable_or_unsupported_latex_gets_one_correction_then_fails_the_attempt(latex):
    writers=[]
    def model(request):
        if request.get('phase')=='review_paragraph':return response(request)
        writers.append(request)
        if request.get('phase')=='repair_formula':
            return {'type':'formula','latex':latex}
        result=response(request)
        result['paragraphs'][0]['blocks'].append({'type':'formula','latex':latex})
        return result
    diagnostics=[]
    with pytest.raises(ValueError,match='^NOTE_FORMULA_INVALID$'):
        mn.generate_part(concepts(),'full',model,diagnostics)
    assert len(writers)==2 and latex == writers[1]['latex']
    assert 'NOTE_FORMULA_INVALID' in mn.RETRYABLE_PART_ERRORS
    assert {'event':'response_invalid','code':'NOTE_FORMULA_INVALID'} in diagnostics
    assert latex not in str(diagnostics)



def test_malformed_formula_correction_keeps_batch_retry():
    attempts=[]
    def model(request):
        if request.get('phase')=='repair_formula':
            return {'type':'formula','latex':''}
        result=response(request)
        if request.get('phase')!='review_paragraph':
            attempts.append(request)
            if len(attempts)==1:
                result['paragraphs'][0]['blocks'].append({'type':'formula','latex':r'x \le y'})
        return result
    result=full(concepts(),model)
    assert result and len(attempts)==2



def test_summary_formula_correction_targets_retained_paragraph_after_reference_cleanup():
    repairs=[]
    def model(request):
        if request.get('phase')=='repair_formula':
            repairs.append(request)
            return {'type':'formula','latex':r'x \leq y'}
        result=response(request)
        if request.get('phase')!='review_paragraph':
            result['paragraphs'][0]['blocks'].append({'type':'formula','latex':r'x \le y'})
            dropped=deepcopy(result['paragraphs'][0])
            dropped.update(heading='丟棄段落',claim_ids=['UNKNOWN'])
            result['paragraphs'].insert(0,dropped)
        return result
    result=mn.generate_part(concepts(),'summary',model)
    assert len(repairs)==1 and repairs[0]['heading']=='訊號'
    assert result[0]['blocks'][1]['latex']==r'x \leq y'



def test_formula_correction_is_local_and_each_formula_is_checked_again():
    calls=[]
    def model(request):
        calls.append(deepcopy(request))
        if request.get('phase')=='repair_formula':
            assert set(request)=={'phase','latex','heading','evidence','correction'}
            assert [e['evidence_id'] for e in request['evidence']]==['E1']
            return {'type':'formula','latex':request['latex'].replace(r'\le ',r'\leq ').replace(r'\ge ',r'\geq ')}
        result=response(request)
        if request.get('phase')!='review_paragraph':
            result['paragraphs'][0]['blocks'] += [
                {'type':'formula','latex':r'x \le y'}, {'type':'formula','latex':r'a \ge b'}]
        return result
    result=mn.generate_part(concepts(),'full',model)
    assert [b['latex'] for b in result[0]['blocks'][1:]]==[r'x \leq y',r'a \geq b']
    reviews=[c for c in calls if c.get('phase')=='review_paragraph']
    assert {'blocks.1.formula','blocks.2.formula'} <= set(reviews[0]['parts'])
    policy={'prompt':nc.PROMPT}
    assert mn.request_contract({'phase':'repair_formula'},policy)[1]['properties']['type']['enum']==['formula']



def test_supported_latex_passes_validation():
    for latex in (r'\text{開放度} = \frac{\text{出口}+\text{進口}}{\text{GDP}} \times 100\%',
                  r'w_{t+1} = w_t - \eta \nabla L(w_t)', r'\sum_{i=1}^{n} x_i^2 \leq \sqrt{n}', r'P(A \mid B)', r'\mathrm{eps}_{\mathrm{hat}}'):
        assert nc.note_latex.problem(latex) is None, latex



def test_norm_delimiter_aliases_render_as_double_bars_in_exports():
    latex=r'\left\lVert x-y\right\rVert^2'
    portable=r'\left\|x-y\right\|^2'
    assert nc.note_latex.problem(latex) is None
    assert nc.note_latex.render_png(latex)==nc.note_latex.render_png(portable)
    assert nc.note_latex.problem(r'\lVertUnexpected x') is not None



def test_concurrent_formula_validation_and_export_keep_identical_layout():
    from concurrent.futures import ThreadPoolExecutor
    formulas=[r'\text{損失}=x^2',r'\frac{x+1}{y+2}',r'\left\lVert x-y\right\rVert^2']
    expected={f:nc.note_latex.render_png(f) for f in formulas}
    def render(index):
        latex=formulas[index%len(formulas)]
        assert nc.note_latex.problem(latex) is None
        return nc.note_latex.render_png(latex)==expected[latex]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(pool.map(render,range(30)))



def test_outline_numbers_chapters_and_sections_from_headings():
    def section(i,level):
        return {'id':str(i),'heading':f'標題{i}','level':level,'blocks':[]}
    paragraphs=[section(0,2),section(1,2),section(2,1),*[section(i,1) for i in range(3,12)],section(12,2)]
    numbers=[o['number'] for o in nc.outline(paragraphs)]
    assert numbers[:4]==['一、','1.1','二、','三、'] and numbers[-2:]==['十一、','11.1']
    assert nc.numbered_heading(nc.outline(paragraphs)[1])=='1.1 標題1'
    assert nc.outline([{'id':'0','heading':'舊','text':'舊版段落','table':None,'formula':None}])==[]



def test_resumed_notes_keep_passed_parts_and_bound_prior_body_context():
    import json
    values=concepts();values[0]['claims']=values[0]['claims'][:2]
    first=mn.generate_part([{**values[0],'claims':values[0]['claims'][:1]}],'full',response)
    before=deepcopy(first)
    plan=[{'heading':'原教材章','level':1,'parts':[[{'concept_id':'c1','claim_ids':[q['claim_id']]}] for q in values[0]['claims']]}]
    requests=[];saved=[]
    def model(request):
        requests.append(request)
        return response(request)
    result=mn.complete_sections(values,plan,model,[first],lambda i,p:saved.append(i))
    assert saved==[1] and first==before
    writer=next(r for r in requests if r.get('phase')!='review_paragraph')
    assert writer['previous_content'][0]['content'][0]['text']=='第 0 個重點'
    assert [p['claim_ids'] for p in result]==[['q0'],['q1']]
    many=[{**first[0],'heading':f'相關重點 {i}',
           'blocks':[{'type':'text','text':'相關重點與權限條件。'*30}]} for i in range(30)]
    snapshot=deepcopy(many)
    context=nc.previous_content(many,'相關重點與權限')
    assert context and len(context)<len(many)
    assert mn.estimate_o200k_tokens(json.dumps(context,ensure_ascii=False))<=nc.PREVIOUS_CONTENT_TOKENS
    assert many==snapshot



@pytest.mark.parametrize('text', ['如原教材第 3 節所定義。', '請見 rfc9213.pdf 第 3 節。', '字面值 `如第 3 節` 保留。', '詳見第三節課的教材。'])
def test_explicit_source_sections_and_literals_keep_their_original_numbers(text):
    values=concepts();values[0]['claims']=values[0]['claims'][:1]
    paragraph=nc.validate({'paragraphs':[para('來源說明',text,['q0'],['e0'])]},values,'full')[0]
    calls=[]
    assert nc.review_paragraph(paragraph,lambda r:calls.append(r) or response(r),
        claims={'q0':values[0]['claims'][0]},kind='full')==[]
    assert len(calls)==1 and text_of(paragraph)==text



def test_structured_output_schema_uses_enum_instead_of_const():
    schema=nc.response_schema(nc.Response)
    assert '"const"' not in str(schema).replace("'",'"')
    blocks=schema['$defs']['Paragraph']['properties']['blocks']['items']['anyOf']
    assert len(blocks)==5



def test_math_cannot_be_disguised_as_code_but_original_command_instruction_is_readable():
    p = {'heading': '指令說明', 'evidence': [{'quote': r'以 \sqrt 指令表示平方根'}],
        'blocks': [{'type': 'text', 'text': r'`\sqrt` 指令表示平方根'}]}
    assert nc.text_formula_issues(p) == []
    p['blocks'][0]['text'] = r'平方根為 `\sqrt{1}`'
    assert nc.text_formula_issues(p)[0]['part'] == 'blocks.0'
    p['blocks'][0]['text'] = r'平方根為 $x^2$'
    assert nc.text_formula_issues(p)



def labeled_sources():
    values = concepts()
    values[0]['claims'] = values[0]['claims'][:1]
    claim = values[0]['claims'][0]
    base = {**claim['evidence'][0], 'source_id': 'source-a', 'normalized_page': 1,
        'source_locator': {'region': [20, 40, 70, 50]}, 'quote': '低成本', 'block_order': 4}
    claim['evidence'] = [base, {**base, 'evidence_id': 'right', 'quote': '高品質',
        'block_order': 5, 'source_locator': {'region': [100, 40, 150, 50]}}]
    claim['context_evidence'] = [
        {**base, 'evidence_id': 'left-header', 'quote': '投入成本', 'block_order': 1,
            'source_locator': {'region': [20, 10, 70, 20]}},
        {**base, 'evidence_id': 'right-header', 'quote': '產出品質', 'block_order': 2,
            'source_locator': {'region': [100, 10, 150, 20]}}]
    return values



@pytest.mark.parametrize('difference', ['source', 'page', 'column', 'below', 'unrelated', 'already_cited'])
def test_column_label_guard_does_not_cite_unrelated_context(difference):
    values = labeled_sources()
    claim = values[0]['claims'][0]
    header = claim['context_evidence'][0]
    if difference == 'source': header['source_id'] = 'source-b'
    if difference == 'page': header['normalized_page'] = 2
    if difference == 'column': header['source_locator']['region'] = [200, 10, 250, 20]
    if difference == 'below': header['source_locator']['region'] = [20, 60, 70, 70]
    if difference == 'unrelated': header['quote'] = '別的欄名'
    raw = para('比較', '投入：低成本', ['q0'], ['e0'])
    if difference == 'already_cited': raw['evidence_ids'].append('left-header')
    paragraph = nc.validate({'paragraphs': [raw]}, values, 'summary')[0]
    assert nc.review_paragraph(paragraph, response, claims={'q0': claim}, kind='summary') == []



@pytest.mark.parametrize('text', [r'結果為 \tfrac{1}{2}', r'機率 `\approx` 0.5', r'語法 `\sqrt`{1}'])
def test_unsupported_or_code_disguised_math_is_not_plain_text(text):
    p = {'heading': '數值', 'evidence': [{'quote': r'機率 \approx 0.5，使用 \sqrt 指令'}],
        'blocks': [{'type': 'text', 'text': text}]}
    assert nc.text_formula_issues(p)



def test_currency_prices_are_not_math_delimiters():
    p = {'heading': '價格', 'evidence': [{'quote': '費用 $10 至 $20'}],
        'blocks': [{'type': 'text', 'text': '費用 $10 至 $20'}]}
    assert nc.text_formula_issues(p) == []



def test_column_label_occurring_inside_cited_body_does_not_replace_header_citation():
    values = labeled_sources()
    claim = values[0]['claims'][0]
    claim['evidence'][0]['quote'] = '這個投入很低'
    paragraph = nc.validate({'paragraphs': [para('比較', '投入：很低', ['q0'], ['e0'])]}, values, 'summary')[0]
    issues = nc.review_paragraph(paragraph, response, claims={'q0': claim}, kind='summary')
    assert issues[0]['missing_evidence_ids'] == ['left-header']



def test_cited_current_header_does_not_import_previous_table_header():
    values = labeled_sources()
    claim = values[0]['claims'][0]
    current = claim['context_evidence'][0]
    claim['context_evidence'].append({**current, 'evidence_id': 'previous-table',
        'source_locator': {'region': [20, 0, 70, 5]}})
    paragraph = nc.validate({'paragraphs': [para('比較', '投入：低成本', ['q0'], ['e0', 'left-header'])]}, values, 'summary')[0]
    assert nc.review_paragraph(paragraph, response, claims={'q0': claim}, kind='summary') == []



@pytest.mark.parametrize('text', [r'字串以 \n 表示換行', r'字串以 `\n` 表示換行', r'讀取 C:\Users\student\notes.txt'])
def test_programming_escapes_and_paths_are_not_formula_markup(text):
    p = {'heading': '程式字串', 'evidence': [{'quote': text}], 'blocks': [{'type': 'text', 'text': text}]}
    assert nc.text_formula_issues(p) == []


@pytest.mark.parametrize("kind", ["summary", "full"])
@pytest.mark.parametrize("bad_part, removed, explanation_survives", [
    ("blocks.0.item.0", {"父條件", "子條件", "孫條件", "另一子條件"}, False),
    ("blocks.0.item.0.0", {"子條件", "孫條件"}, False),
    ("blocks.1.row.0", {"錯誤列"}, False),
    ("blocks.1.headers", {"欄位", "錯誤列", "正確列"}, False),
    ("blocks.1.caption", {"比較表"}, True),
    ("blocks.2.formula", {r"a=\frac{b}{c}"}, False),
    ("blocks.3.callout", {"案例標籤"}, True),
    ("heading", {"候選標題"}, True),
])
def test_single_review_removes_only_unsupported_positions_and_dependent_content(kind, bad_part, removed, explanation_survives):
    values = concepts()
    raw = para("候選標題", "可保留正文", ["C1", "C2", "C3"], ["E1", "E2", "E3"])
    raw["blocks"] = [
        {"type": "bullets", "items": [
            {"text": "父條件", "children": [{"text": "子條件", "children": [{"text": "孫條件"}]},
                {"text": "另一子條件", "children": []}]}, {"text": "獨立條列", "children": []}]},
        {"type": "table", "caption": "比較表", "headers": ["欄位"], "rows": [["錯誤列"], ["正確列"]]},
        {"type": "formula", "latex": r"a=\frac{b}{c}"},
        {"type": "callout", "style": "case_study", "title": "案例標籤", "items": [{"text": "案例正文", "children": []}]},
        {"type": "text", "text": "可保留正文"},
    ]
    position = {"blocks.1.headers": "blocks.1.row.0", "blocks.1.caption": "blocks.1.row.0",
        "blocks.3.callout": "blocks.3.item.0", "heading": "blocks.4"}.get(bad_part, bad_part)
    calls = []
    def model(request):
        calls.append(request)
        if request.get("phase") != "review_paragraph":
            return {"paragraphs": [deepcopy(raw)]}
        result = response(request)
        for check in result["checks"]:
            if check["part"] == bad_part:
                check.update(supported=False, reason="該位置不受原文支持")
        for item in result["coverage"]:
            item["note_parts"] = [position if item["claim"] == "C1" else "blocks.4"]
        return result
    diagnostics = []
    if kind == "summary":
        result = check_written_paragraphs(values, kind, model, diagnostics)
    else:
        plan = mn.plan_sections(values, sections(values), kind)
        result = mn.complete_sections(values, plan, model, [], lambda *args: None, diagnostics, kind)
    assert len(result) == 1
    assert [request.get("phase", "write") for request in calls] == ["write", "review_paragraph"]
    paragraph = result[0]
    contents = {paragraph["heading"], *(value for item in nc.content_parts(paragraph)
        for value in [item.get("text"), item.get("title"), item.get("latex"), *item.get("cells", [])] if value)}
    assert all(text not in contents for text in removed)
    assert "可保留正文" in contents and "獨立條列" in contents
    if bad_part == "blocks.0.item.0.0":
        assert "父條件" in contents and "另一子條件" in contents
    if bad_part == "blocks.1.row.0":
        assert "正確列" in contents and "欄位" in contents
    if bad_part == "blocks.3.callout":
        assert paragraph["blocks"][3] == {"type": "bullets", "items": [{"text": "案例正文", "children": []}]}
    assert paragraph["claim_ids"] == ["q0", "q1", "q2"]
    assert paragraph["evidence_ids"] == ["e0", "e1", "e2"]
    assert paragraph["_explained_claim_ids"] == (["q0", "q1", "q2"] if explanation_survives else ["q1", "q2"])
    assert diagnostics[0]["issues"][0]["part"] == bad_part
    assert "該位置不受原文支持" not in str(diagnostics)


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_unsupported_paragraph_is_dropped_once_and_supported_neighbors_keep_sources(kind):
    calls, saved, diagnostics = [], [], []
    def model(request):
        calls.append(request)
        result = response(request)
        if request.get("phase") == "review_paragraph" and text_of(request["paragraph"]) == "第 1 個重點":
            result["checks"][1].update(supported=False, reason="與原文不符")
        return result
    values = concepts()
    result = mn.complete_sections(values, mn.plan_sections(values, sections(values), kind), model, [],
        lambda i, p: saved.append((i, deepcopy(p))), diagnostics, kind)
    assert [text_of(p) for p in result] == ["第 0 個重點", "第 2 個重點"]
    assert [p["id"] for p in result] == ["0", "1"]
    assert [p["claim_ids"] for p in result] == [["q0"], ["q2"]]
    assert [p["evidence_ids"] for p in result] == [["e0"], ["e2"]]
    assert [r.get("phase", "write") for r in calls] == ["write", "review_paragraph", "review_paragraph", "review_paragraph"]
    assert saved[0][0] == 0 and len(saved[0][1]) == 2
    assert diagnostics[-1] == {"event": "paragraph_dropped", "reason": "no_supported_content", "claims": ["q1"]}


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_all_unsupported_content_stops_after_full_notes_supplement(kind):
    values = concepts()
    values[0]["claims"] = values[0]["claims"][:1]
    calls, saved = [], []
    def model(request):
        calls.append(request)
        result = response(request)
        if request.get("phase") == "review_paragraph":
            result["checks"][1].update(supported=False, reason="無原文支持")
        return result
    result = mn.complete_sections(values, mn.plan_sections(values, sections(values), kind), model, [],
        lambda i, p: saved.append((i, p)), kind=kind)
    assert result == [] and saved == [(0, [])]
    assert len(calls) == (4 if kind == "full" else 2)


@pytest.mark.parametrize("recovered", [True, False])
def test_full_notes_supplement_only_missing_concepts_and_checkpoint_the_result(recovered):
    values = [{**c, "claims": c["claims"][:1]} for c in three_concepts()]
    writers, checkpoints = [], []
    def model(request):
        result = response(request)
        if request.get("phase") != "review_paragraph":
            writers.append(request)
        elif text_of(request["paragraph"]) == values[1]["claims"][0]["text"]:
            if len(writers) == 1 or not recovered:
                for item in result["coverage"]:
                    item.update(explained=False, note_parts=[], reason="尚未解說")
        return result
    plan = mn.plan_sections(values, sections(values))
    saved = []
    result = mn.complete_sections(values, plan, model, saved,
        lambda i, p: checkpoints.append(deepcopy(p)))
    assert len(writers) == 2
    assert [c["label"] for c in writers[1]["concepts"]] == [values[1]["label"]]
    assert [p["claim_ids"] for p in result[:3]] == [[c["claims"][0]["claim_id"]] for c in values]
    assert result[1]["_explained_claim_ids"] == []
    assert result[-1]["_explained_claim_ids"] == ([values[1]["claims"][0]["claim_id"]] if recovered else [])
    assert len(checkpoints) == 1 and len(checkpoints[0]) == 4
    def no_more_calls(request):
        pytest.fail("completed batch must not repeat its supplement")
    assert mn.complete_sections(values, plan, no_more_calls, saved, lambda *args: None) == result


def test_supplement_format_retry_keeps_supported_initial_draft():
    values = [{**c, "claims": c["claims"][:1]} for c in three_concepts()]
    writers = []
    def model(request):
        if request.get("phase") != "review_paragraph":
            writers.append(request)
            if len(writers) == 2:
                raise ValueError("NOTE_REVIEW_INVALID")
        result = response(request)
        if request.get("phase") == "review_paragraph" and len(writers) == 1:
            if text_of(request["paragraph"]) == values[1]["claims"][0]["text"]:
                for item in result["coverage"]:
                    item.update(explained=False, note_parts=[], reason="尚未解說")
        return result
    result = full(values, model)
    assert [[c["label"] for c in request["concepts"]] for request in writers] == [
        [c["label"] for c in values], [values[1]["label"]], [values[1]["label"]]]
    assert [p["claim_ids"] for p in result[:3]] == [[c["claims"][0]["claim_id"]] for c in values]
    assert result[1]["_explained_claim_ids"] == []
    assert result[-1]["_explained_claim_ids"] == [values[1]["claims"][0]["claim_id"]]


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_unexplained_claim_preserves_supported_text_and_real_citations(kind):
    values = concepts()
    calls = []
    def model(request):
        calls.append(request)
        result = response(request)
        if request.get("phase") == "review_paragraph":
            result["coverage"][0].update(explained=False, note_parts=[], reason="僅說明部分內容")
        return result
    result = mn.generate_part(values, kind, model)
    assert [p["claim_ids"] for p in result] == [["q0"], ["q1"], ["q2"]]
    assert all(p["_explained_claim_ids"] == [] for p in result)
    assert [text_of(p) for p in result] == ["第 0 個重點", "第 1 個重點", "第 2 個重點"]
    assert len(calls) == 4
    omitted = nc.validate(response({"concepts": [{**values[0], "claims": values[0]["claims"][:1]}]}), values, kind)
    assert omitted[0]["claim_ids"] == ["q0"]


def test_coverage_can_use_supported_earlier_paragraphs_without_rechecking_them():
    values = concepts()
    values[0]["claims"] = values[0]["claims"][:1]
    reviews = []
    def model(request):
        if request.get("phase") == "review_paragraph":
            reviews.append(request)
            result = response(request)
            result["coverage"][0]["note_parts"] = ["blocks.0"] if len(reviews) == 1 else ["related.0.blocks.0", "blocks.0"]
            return result
        original = response(request)["paragraphs"][0]
        return {"paragraphs": [with_text(original, "條件說明"), with_text(original, "結論說明")]}
    result = full(values, model)
    assert len(reviews) == len(result) == 2
    assert reviews[0]["coverage_claims"] == reviews[1]["coverage_claims"] == ["C1"]
    assert reviews[1]["related_paragraphs"][0]["content"][0]["text"] == "條件說明"
    assert all(p["_explained_claim_ids"] == ["q0"] for p in result)


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_missing_reference_is_checked_once_and_unsupported_sentence_is_removed(kind):
    values = concepts()
    values[0]["claims"] = values[0]["claims"][:1]
    claim = values[0]["claims"][0]
    claim["evidence"][0].update(quote="that field be ignored", source_id="source-a", page=4, block_order=11)
    claim["context_evidence"] = [
        {**claim["evidence"][0], "evidence_id": "norm", "quote": "MUST", "block_order": 18},
        {**claim["evidence"][0], "evidence_id": "foreign", "quote": "SHOULD", "source_id": "source-b"},
        {**claim["evidence"][0], "evidence_id": "next", "quote": "MAY", "page": 5},
    ]
    calls = []
    def model(request):
        calls.append(request)
        if request.get("phase") == "review_paragraph":
            assert [(e["ref"], e["quotes"]) for e in request["reference_context"]] == [("X1", ["MUST"])]
            result = response(request)
            result["checks"][1].update(supported=False, missing_evidence=["X1"], reason="漏引規範詞")
            return result
        raw = para("欄位", "快取便忽略欄位", ["C1"], ["E1"])
        raw["blocks"].append({"type": "text", "text": "此處說明欄位"})
        return {"paragraphs": [raw]}
    result = check_written_paragraphs(values, kind, model) if kind == "summary" else mn.generate_part(values, kind, model)
    assert len(calls) == 2 and result[0]["blocks"] == [{"type": "text", "text": "此處說明欄位"}]
    assert result[0]["evidence_ids"] == ["e0"] and result[0]["_explained_claim_ids"] == []
    schema = mn.request_contract(calls[1], {"review_prompt": nc.REVIEW_PROMPT})[1]
    assert schema["$defs"]["ContentCheck"]["properties"]["missing_evidence"]["items"]["enum"] == ["X1"]


def test_formula_numbers_are_checked_by_program_even_when_model_accepts_them():
    values = concepts()
    values[0]["claims"] = values[0]["claims"][:1]
    calls = []
    def model(request):
        calls.append(request)
        result = response(request)
        if request.get("phase") != "review_paragraph":
            result["paragraphs"][0]["blocks"].append({"type": "formula", "latex": r"\frac{a}{b}\times 100\%"})
        return result
    result = mn.generate_part(values, "full", model)
    assert len(calls) == 2 and result[0]["blocks"] == [{"type": "text", "text": "第 0 個重點"}]
    paragraph = result[0]
    paragraph["evidence"][0]["quote"] = "平方為 x²，開放度乘以 100%"
    paragraph["blocks"].append({"type": "formula", "latex": "x^2"})
    assert nc.formula_literal_issues(paragraph) == []


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_unqualified_source_section_reference_is_removed_without_changing_original(kind):
    values = concepts()
    values[0]["claims"] = values[0]["claims"][:1]
    values[0]["claims"][0]["evidence"][0]["quote"] = "The target field is defined in Section 3."
    calls = []
    def model(request):
        calls.append(request)
        result = response(request)
        if request.get("phase") != "review_paragraph":
            result["paragraphs"][0]["blocks"] = [{"type": "text", "text": "目標欄位如第 3 節所定義。"},
                {"type": "text", "text": "此處介紹目標欄位。"}]
        return result
    result = check_written_paragraphs(values, kind, model) if kind == "summary" else mn.generate_part(values, kind, model)
    assert result[0]["blocks"] == [{"type": "text", "text": "此處介紹目標欄位。"}]
    assert len(calls) == 2
    assert values[0]["claims"][0]["evidence"][0]["quote"] == "The target field is defined in Section 3."


@pytest.mark.parametrize("kind", ["summary", "full"])
def test_column_label_guard_removes_unsupported_table_but_keeps_independent_text(kind):
    values = labeled_sources()
    calls = []
    def model(request):
        calls.append(request)
        if request.get("phase") == "review_paragraph":
            result = response(request)
            result["coverage"][0]["note_parts"] = ["blocks.1"]
            return result
        raw = para("比較", "可保留的來源描述", ["C1"], ["E1", "E2"])
        raw["blocks"].insert(0, {"type": "table", "caption": None, "headers": ["投入", "產出"], "rows": [["低成本", "高品質"]]})
        return {"paragraphs": [raw]}
    result = check_written_paragraphs(values, kind, model) if kind == "summary" else mn.generate_part(values, kind, model)
    assert len(calls) == 2 and len(calls[1]["evidence"]) == 2
    assert result[0]["blocks"] == [{"type": "text", "text": "可保留的來源描述"}]
    assert result[0]["evidence_ids"] == ["e0", "right"]
    assert result[0]["_explained_claim_ids"] == ["q0"]


def test_column_label_uses_nearest_matching_header_not_an_earlier_table():
    values = labeled_sources()
    claim = values[0]["claims"][0]
    current = claim["context_evidence"][0]
    claim["context_evidence"].append({**current, "evidence_id": "previous-table", "source_locator": {"region": [20, 0, 70, 5]}})
    paragraph = nc.validate({"paragraphs": [para("比較", "投入：低成本", ["q0"], ["e0"])]}, values, "summary")[0]
    issues = nc.review_paragraph(paragraph, response, claims={"q0": claim}, kind="summary")
    assert issues[0]["missing_evidence_ids"] == ["left-header"]


def test_summary_selects_each_whole_chapter_even_when_every_candidate_fits():
    values = three_concepts()
    chapter_plan = [
        {"section_id": "chapter-a", "heading": "第一章", "level": 1, "parent_id": None, "claim_ids": []},
        {"section_id": "a1", "heading": "1.1", "level": 2, "parent_id": "chapter-a", "claim_ids": ["q00", "q01"]},
        {"section_id": "a2", "heading": "1.2", "level": 2, "parent_id": "chapter-a", "claim_ids": ["q10", "q11"]},
        {"section_id": "chapter-b", "heading": "第二章", "level": 1, "parent_id": None, "claim_ids": ["q20", "q21"]},
    ]
    calls = []
    def model(request):
        calls.append(request)
        if request.get("phase") == "select_summary":
            remaining = 3
            choices = []
            for item in request["concepts"]:
                chosen = [q["ref"] for q in item["claims"]][:remaining]
                remaining -= len(chosen)
                choices.append({"concept": item["ref"], "claim_ids": chosen})
            return {"concepts": choices}
        return response(request)
    plan = mn.summary_plan(values, chapter_plan, model, input_budget=10**6)
    selections = [request for request in calls if request.get("phase") == "select_summary"]
    assert len(selections) == 2
    assert [[c["label"] for c in request["concepts"]] for request in selections] == [["概念0", "概念1"], ["概念2"]]
    assert [section["claim_ids"] for section in plan] == [[], ["q00", "q01"], ["q10"], ["q20", "q21"]]
    result = mn.complete_sections(values, plan, model, [], lambda *args: None, kind="summary")
    assert [p["level"] for p in result] == [2, 2, 2, 1, 1]
    assert len([request for request in calls if request.get("phase") == "select_summary"]) == 2


def test_summary_selection_has_a_whole_chapter_limit_and_can_leave_concepts_unselected():
    values = three_concepts()
    calls = []
    def model(request):
        calls.append(request)
        return {"concepts": [{"concept": c["ref"], "claim_ids": [q["ref"] for q in c["claims"]] if i < 2 else []}
            for i, c in enumerate(request["concepts"])]}
    selected = mn.select_summary(values, model, input_budget=10**6)
    assert len(calls) == 1 and calls[0]["max_claims"] == 5
    assert [c["concept_id"] for c in selected] == ["k0", "k1"]
    assert sum(len(c["claims"]) for c in selected) == 4


def test_summary_selection_keeps_raw_context_outside_the_selection_request():
    values = three_concepts()
    values[0]["claims"] = values[0]["claims"][:1]
    for c in values:
        for q in c["claims"]:
            q["evidence"][0]["source_name"] = c["label"] + ".pdf"
            q["context_evidence"] = [{**q["evidence"][0], "evidence_id": "ctx" + q["claim_id"], "quote": "同頁補充原文。" * 400}]
    calls = []
    def model(request):
        calls.append(request)
        if request.get("phase") == "select_summary":
            return {"concepts": [{"concept": c["ref"], "claim_ids": [c["claims"][-1]["ref"]]} for c in request["concepts"]]}
        return response(request)
    result = summary(values, model, 10**6)
    selection = calls[0]
    assert selection["phase"] == "select_summary"
    assert [c["label"] for c in selection["concepts"]] == ["概念0", "概念1", "概念2"]
    assert selection["concepts"][0]["claims"][0] == {"ref": "C1", "text": "概念0的第 0 點", "sources": ["概念0.pdf"]}
    assert "同頁補充原文" not in str(selection)
    assert {q for p in result for q in p["claim_ids"]} == {"q00", "q11", "q21"}
    writer = next(r for r in calls if r.get("phase") is None)
    assert any("同頁補充原文" in q for e in writer["evidence"] for q in e["quotes"])


def test_selection_batches_preserve_each_candidate_once_within_actual_prompt_budget():
    values = three_concepts()
    entries = [(c, c["claims"]) for c in values]
    budget = mn.estimate_prompt_tokens(nc.SELECTION_PROMPT, mn._selection_request(entries[:1])[0]) + 10
    batches = mn._selection_batches(entries, budget)
    offered = [q["claim_id"] for batch in batches for _, claims in batch for q in claims]
    assert len(batches) > 1
    assert offered == [q["claim_id"] for c in values for q in c["claims"]]
    assert all(mn.estimate_prompt_tokens(nc.SELECTION_PROMPT, mn._selection_request(batch)[0]) <= budget for batch in batches)


def test_selected_summary_writes_complete_sources_in_parts_and_resumes_without_selection():
    values = three_concepts()
    for c in values:
        for q in c["claims"]:
            q["evidence"][0]["quote"] = "較長原文，寫作時完整帶入。" * 80
    unit = {**values[0], "claims": values[0]["claims"][:1]}
    budget = mn.estimate_prompt_tokens(nc.PROMPT, mn.model_request([unit], "summary")[0]) + 50
    calls = []
    def model(request):
        calls.append(request)
        if request.get("phase") == "select_summary":
            return {"concepts": [{"concept": c["ref"], "claim_ids": [c["claims"][0]["ref"]]} for c in request["concepts"]]}
        return response(request)
    plan = mn.summary_plan(values, sections(values), model, input_budget=budget)
    assert len(calls) == 1
    parts = [part for section in plan for part in section["parts"]]
    assert [[c["claim_ids"] for c in part] for part in parts] == [[["q00"]], [["q10"]], [["q20"]]]
    first = mn.generate_part([{**values[0], "claims": values[0]["claims"][:1]}], "summary", model)
    before = deepcopy(first)
    saved = []
    result = mn.complete_sections(values, plan, model, [first], lambda i, p: saved.append(i), kind="summary")
    assert saved == [1, 2] and first == before
    assert [p["claim_ids"] for p in result] == [["q00"], ["q10"], ["q20"]]
    assert len([r for r in calls if r.get("phase") == "select_summary"]) == 1
    writers = [r for r in calls if r.get("phase") is None]
    assert len(writers) == 3
    assert all(e["quotes"] == ["較長原文，寫作時完整帶入。" * 80] for writer in writers for e in writer["evidence"])


def test_shared_candidates_are_selected_once_without_forcing_every_concept_back_in():
    values = three_concepts()
    values[0]["claims"].extend(values[1]["claims"])
    values[1]["claims"] = [deepcopy(values[0]["claims"][-1])]
    values[2]["claims"] = [deepcopy(values[0]["claims"][0])]
    calls = []
    def model(request):
        calls.append(request)
        item = request["concepts"][0]
        return {"concepts": [{"concept": item["ref"], "claim_ids": [q["ref"] for q in item["claims"][:3]]}]}
    selected = mn.select_summary(values, model, input_budget=10**6)
    assert len(calls) == 1 and len(calls[0]["concepts"]) == 1
    assert [c["concept_id"] for c in selected] == ["k0", "k2"]
    assert [q["claim_id"] for q in selected[0]["claims"]] == ["q00", "q01", "q10"]
    assert selected[1]["claims"][0]["claim_id"] == "q00"


@pytest.mark.parametrize("bad", ["missing_concept", "foreign_claim", "too_few", "too_many", "duplicate"])
def test_summary_selection_rejects_invalid_chapter_picks_after_one_correction(bad):
    values = three_concepts()
    calls = []
    def model(request):
        calls.append(request)
        picks = [{"concept": c["ref"], "claim_ids": [c["claims"][0]["ref"]]} for c in request["concepts"]]
        if bad == "missing_concept": picks.pop()
        if bad == "foreign_claim": picks[0]["claim_ids"] = [request["concepts"][1]["claims"][0]["ref"]]
        if bad == "too_few": picks[0]["claim_ids"] = []
        if bad == "too_many":
            picks = [{"concept": c["ref"], "claim_ids": [q["ref"] for q in c["claims"]]} for c in request["concepts"]]
        if bad == "duplicate": picks[0]["claim_ids"] *= 2
        return {"concepts": picks}
    with pytest.raises(ValueError, match="^NOTE_SELECTION_INVALID$"):
        mn.select_summary(values, model, input_budget=10**6)
    assert len(calls) == 2 and "correction" in calls[1]


def test_invalid_batch_retries_without_rerunning_the_previous_checkpoint():
    values = [{**c, "claims": c["claims"][:1]} for c in three_concepts()]
    plan = [{"heading": "原教材章", "level": 1, "parts": [
        [{"concept_id": c["concept_id"], "claim_ids": [c["claims"][0]["claim_id"]]}] for c in values]}]
    writers, saved, diagnostics = [], [], []
    def model(request):
        if request.get("phase") == "review_paragraph":
            return response(request)
        writers.append(request)
        count = sum(w["concepts"][0]["label"] == "概念1" for w in writers)
        if request["concepts"][0]["label"] == "概念1" and count <= 4:
            return {"paragraphs": []}
        return response(request)
    result = mn.complete_sections(values, plan, model, [], lambda i, p: saved.append(i), diagnostics)
    assert saved == [0, 1, 2] and len(result) == 3
    assert [w["concepts"][0]["label"] for w in writers if "correction" not in w] == ["概念0", "概念1", "概念1", "概念1", "概念2"]
    assert diagnostics[-1] == {"event": "part_attempts", "part": 1, "attempts": 3,
        "failed": ["NOTE_FORMAT_INVALID", "NOTE_FORMAT_INVALID"], "succeeded": True}


def test_failed_new_batch_keeps_existing_checkpoint_and_its_sources_unchanged():
    values = [{**c, "claims": c["claims"][:1]} for c in three_concepts()]
    first = mn.generate_part(values[:1], "summary", response)
    results = [first]
    before = deepcopy(results)
    plan = [{"heading": "原教材章", "level": 1, "parts": [
        [{"concept_id": c["concept_id"], "claim_ids": [c["claims"][0]["claim_id"]]}] for c in values]}]
    def model(request):
        raise ValueError("NOTE_INPUT_TOO_LARGE")
    saved = []
    with pytest.raises(ValueError, match="NOTE_INPUT_TOO_LARGE"):
        mn.complete_sections(values, plan, model, results, lambda *args: saved.append(args), kind="summary")
    assert results == before and saved == []


def test_writer_gets_the_original_section_heading_and_program_sets_all_three_levels():
    values = concepts()
    plan = mn.plan_sections(values, [{"section_id": f"s{i}", "heading": f"原節 {i}", "level": i + 1,
        "parent_id": None if i == 0 else f"s{i-1}", "claim_ids": [q["claim_id"]]}
        for i, q in enumerate(values[0]["claims"])])
    writers = []
    def model(request):
        if request.get("phase") != "review_paragraph":
            writers.append(request)
        return response(request)
    result = mn.complete_sections(values, plan, model, [], lambda *args: None)
    assert [r["previous_section"] for r in writers] == ["原節 0", "原節 1", "原節 2"]
    assert [p["level"] for p in result] == [1, 2, 3]

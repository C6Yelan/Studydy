"""來源、全份涵蓋、A4 正文與註記精確對應的回歸。"""
from copy import deepcopy
import pymupdf
import pytest
from runtime import note_content as nc, material_notes as mn


def concepts():
    return [{"concept_id": "c1", "label": "訊號", "claims": [
        {"claim_id": f"q{i}", "text": f"第 {i} 個重點", "evidence": [{"evidence_id": f"e{i}", "quote": f"原文 {i}", "page": i + 1}]}
        for i in range(3)]}]


def response(request):
    if request.get("phase") == "review_paragraph":
        return {"checks": [{"part": part, "supported": True, "reason": ""} for part in request["parts"]]}
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


@pytest.mark.parametrize("failure", ["foreign_claim", "foreign_evidence", "wrong_claim_evidence", "missing_claim", "ragged_table"])
def test_invalid_model_output_is_not_published(failure):
    value = response({"concepts": concepts()})
    if failure == "foreign_claim": value["paragraphs"][0]["claim_ids"] = ["foreign"]
    if failure == "foreign_evidence": value["paragraphs"][0]["evidence_ids"] = ["foreign"]
    if failure == "wrong_claim_evidence": value["paragraphs"][0]["evidence_ids"] = ["e1"]
    if failure == "missing_claim": value["paragraphs"].pop()
    if failure == "ragged_table": value["paragraphs"][0]["table"] = {"headers": ["a", "b"], "rows": [["only one"]]}
    with pytest.raises(ValueError): nc.validate(value, concepts(), "full")


def test_summary_body_has_one_a4_page_with_readable_chinese_and_table():
    paragraph = {"heading": "複習筆記", "text": "保留來源中的條件與例外。", "formula": "速度 = 距離 ÷ 時間",
                 "table": {"headers": ["條件", "結果"], "rows": [["固定時間", "距離增加"]]}}
    raw = nc.summary_pdf([paragraph])
    with pymupdf.open(stream=raw) as doc:
        assert len(doc) == 1 and doc[0].rect == pymupdf.paper_rect("a4")
        text = doc[0].get_text()
        assert "複習筆記" in text and "距離增加" in text
        spans = [s for b in doc[0].get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
        assert min(s["size"] for s in spans) >= 12
    with pytest.raises(ValueError, match="NOTE_SUMMARY_TOO_LONG"):
        nc.summary_pdf([{**paragraph, "text": "超長正文不可以靠縮字或裁切發布。" * 200}])


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


def test_invalid_response_gets_one_correction_and_no_fallback():
    calls = []
    def answer(request):
        calls.append(request)
        return {"paragraphs": []}
    with pytest.raises(ValueError): full(concepts(), answer)
    assert len(calls) == 2 and "correction" in calls[1]


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


def test_large_summary_selects_points_then_writes_with_complete_original_evidence():
    values=concepts()
    for q in values[0]['claims']:
        q['evidence'][0]['quote']='保留完整條件與數字，不能裁切來源。'*450
    calls=[]
    def model(request):
        calls.append(request)
        if request.get('phase')=='select_summary':
            return {'claim_ids':[request['candidates'][0]['ref']]}
        return response(request)
    result=mn.compute_summary(values,model,input_budget=mn.BATCH_TOKENS)
    assert len(calls)==4 and calls[0]['phase']=='select_summary'
    assert calls[1]['evidence'][0]['quotes']==[values[0]['claims'][0]['evidence'][0]['quote']]
    assert result[0]['claim_ids']==['q0']


def test_summary_selection_cannot_invent_ids_or_skip_validation():
    values=concepts()
    for q in values[0]['claims']:
        q['evidence'][0]['quote']='保留完整來源。'*1200
    calls=[]
    def model(request):
        calls.append(request)
        return {'claim_ids':['not-in-input']}
    with pytest.raises(ValueError,match='NOTE_SELECTION_INVALID'):
        mn.compute_summary(values,model,input_budget=mn.BATCH_TOKENS)
    assert len(calls)==2


def test_summary_overflow_correction_keeps_font_and_makes_a_shorter_body():
    calls=[]
    def model(request):
        calls.append(request)
        value=response(request)
        if len(calls)==1:
            value['paragraphs'][0]['text']='太長的摘要正文。'*500
        return value
    result=mn.compute_summary(concepts(),model,input_budget=mn.BATCH_TOKENS)
    assert len(calls)==5 and calls[1]['correction']['problem']=='NOTE_SUMMARY_TOO_LONG'
    assert len(pymupdf.open(stream=nc.summary_pdf(result)))==1


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


@pytest.mark.parametrize('bad_part', ['text', 'formula', 'table.row.0'])
def test_content_mismatch_regenerates_the_paragraph_and_rechecks_every_part(bad_part):
    values=concepts()[:1]
    values[0]['claims']=values[0]['claims'][:1]
    calls=[]
    writers=[]
    def model(request):
        calls.append(request)
        if request.get('phase')=='review_paragraph':
            return {'checks':[{'part':part,'supported':not (len(writers)==1 and part==bad_part),
                'reason':'該條件與數值的配對不受同一範例支持。' if len(writers)==1 and part==bad_part else ''}
                for part in request['parts']]}
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
        return {'checks':checks}
    with pytest.raises(nc.ReviewInvalid,match='NOTE_REVIEW_INVALID'):
        nc.review_paragraph(paragraph,model)


def test_content_review_sees_only_cited_original_and_keeps_all_table_rows():
    values=concepts()
    raw=response({'concepts':values})
    raw['paragraphs'][0].update(formula='原文公式',table={'headers':['項目','內容'],'rows':[['甲','原文甲'],['乙','原文乙']]})
    p=nc.validate(raw,values,'full')[0]
    seen=[]
    def model(request):
        seen.append(request)
        return response(request)
    assert nc.review_paragraph(p,model)==[]
    assert seen[0]['parts']==['heading','text','formula','table.headers','table.row.0','table.row.1']
    assert [q for e in seen[0]['evidence'] for q in e['quotes']]==['原文 0']
    assert seen[0]['paragraph']['formula']=='原文公式'
    assert seen[0]['paragraph']['table']==raw['paragraphs'][0]['table']


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
            return {'checks':[{'part':p,'supported':not (rejected and p=='text'),
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
    nc.review_paragraph(paragraph,model)
    for key in ('source_id','source_name','page','block_order','region','quotes'):
        assert checks[0]['evidence'][0][key]==source[key]
    assert e==original and paragraph['evidence'][0]==original


def test_summary_overflow_feedback_uses_measured_page_ratio():
    p={'heading':'合成稿','text':'合成測試文字。'*400,'formula':None,'table':None}
    layout=nc.summary_layout([p])
    assert not layout['fits'] and layout['used_height_pt']>layout['available_height_pt']
    target=(layout['available_height_pt']-12*1.4)/layout['used_height_pt']
    assert layout['target_height_ratio']==pytest.approx(target,abs=.0001)
    assert layout['suggested_characters']==pytest.approx(layout['current_characters']*target,abs=1)
    assert 450<layout['suggested_characters']<layout['current_characters']
    with pytest.raises(nc.SummaryTooLong) as error:nc.summary_pdf([p])
    assert error.value.layout==layout
    request=mn._layout_request({'kind':'summary'},[p],layout)
    assert request['correction']['layout']==layout
    assert request['correction']['previous_body'][0]['text']==p['text']


@pytest.mark.parametrize('all_ids_present',[False,True])
def test_summary_with_room_tries_to_cover_more_available_points(all_ids_present):
    writers=[]
    def model(request):
        if request.get('phase')=='review_paragraph':return response(request)
        writers.append(request)
        result=response(request)
        if len(writers)==1 and not all_ids_present:result['paragraphs']=result['paragraphs'][:1]
        return result
    result=mn.compute_summary(concepts(),model,input_budget=mn.BATCH_TOKENS)
    assert len(writers)==2
    assert writers[1]['correction']['problem']=='NOTE_SUMMARY_ROOM'
    assert writers[1]['correction']['layout']['target_height_ratio']>1
    assert {q for p in result for q in p['claim_ids']}=={'q0','q1','q2'}


def test_reflowed_summary_can_repair_its_own_references_after_earlier_repair():
    writers=[]
    def model(request):
        if request.get('phase')=='review_paragraph':return response(request)
        writers.append(request)
        result=response(request)
        if len(writers) in (1,3):result['paragraphs'][0]['evidence_ids']=['unknown']
        if len(writers)==2:result['paragraphs'][0]['text']='合成超頁正文。'*500
        return result
    result=mn.compute_summary(concepts(),model,input_budget=mn.BATCH_TOKENS)
    assert len(writers)==4
    assert writers[3]['correction']['problem']=='NOTE_REFERENCE_INVALID'
    assert writers[3]['correction']['layout']['height_ratio']>1
    assert writers[3]['correction']['previous_response']['paragraphs'][0]['evidence_ids']==['unknown']
    assert nc.summary_layout(result)['fits']


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


def test_summary_reflows_content_repair_that_exceeds_one_page_then_rechecks():
    values=concepts();values[0]['claims']=values[0]['claims'][:1]
    writers=[];reviews=[]
    def model(request):
        if request.get('phase')=='review_paragraph':
            reviews.append(request)
            bad=len(reviews)==1
            return {'checks':[{'part':p,'supported':not (bad and p=='text'),'reason':'修正來源條件。' if bad and p=='text' else ''} for p in request['parts']]}
        writers.append(request)
        result=response(request)
        if request.get('correction',{}).get('problem')=='NOTE_CONTENT_UNSUPPORTED':
            result['paragraphs'][0]['text']='合成長稿。'*700
        return result
    result=mn.compute_summary(values,model,input_budget=mn.BATCH_TOKENS)
    assert len(writers)==4 and len(reviews)==3
    assert writers[3]['correction']['problem']=='NOTE_SUMMARY_TOO_LONG'
    assert writers[3]['correction']['layout']['height_ratio']>1
    assert nc.summary_layout(result)['fits']


def test_summary_uses_available_context_instead_of_full_note_batch_limit():
    values=concepts()
    for q in values[0]['claims']:q['evidence'][0]['quote']='合成來源完整條件。'*900
    request,_=mn.model_request(values,'summary')
    size=mn.estimate_prompt_tokens(nc.PROMPT,request)
    assert size>mn.BATCH_TOKENS
    calls=[]
    def model(request):
        calls.append(request)
        return response(request)
    result=mn.compute_summary(values,model,input_budget=size+100)
    assert len(result)==3
    assert all(r.get('phase')!='select_summary' for r in calls)
    assert len(calls[0]['concepts'][0]['claims'])==3

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
    assert len(result) == 3 and len(calls) == 3
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
    result=mn.compute_summary(values,model)
    assert len(calls)==2 and calls[0]['phase']=='select_summary'
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
        mn.compute_summary(values,model)
    assert len(calls)==2


def test_summary_overflow_correction_keeps_font_and_makes_a_shorter_body():
    calls=[]
    def model(request):
        calls.append(request)
        value=response(request)
        if len(calls)==1:
            value['paragraphs'][0]['text']='太長的摘要正文。'*500
        return value
    result=mn.compute_summary(concepts(),model)
    assert len(calls)==2 and calls[1]['correction']['problem']=='NOTE_SUMMARY_TOO_LONG'
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

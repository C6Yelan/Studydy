"""診斷使用逐重點真實資料；難度短編號必須完整映回來源概念。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from learning_adaptation.learning_insights import diagnosis_input, template_diagnosis
from learning_adaptation.concept_difficulty import difficulty_batches, generate_difficulty
from runtime.local_ai_config import _runtime_lock
from runtime.material_runtime import validate_runtime_lock


def document():
    return {"concepts": [
        {"concept_id": "real-concept-one", "label": "快取有效期", "claims": [
            {"claim_id": "p1", "text": "freshness_lifetime > current_age 時為新鮮。", "evidence_refs": ["e1"]},
            {"claim_id": "p2", "text": "過期後需要重新驗證。", "evidence_refs": ["e1"]}]},
        {"concept_id": "real-concept-two", "label": "未測概念", "claims": [{"claim_id": "p3", "text": "重點。", "evidence_refs": ["e1"]}]},
    ], "relations": [{"type": "prerequisite", "source_concept_id": "real-concept-one", "target_concept_id": "real-concept-two"}],
        "initial_learning_path": [{"concept_id": "real-concept-one"}, {"concept_id": "real-concept-two"}],
        "evidence": [{"evidence_id": "e1", "page": 1}],
        "input_binding": {"bundle": {"pages": [{"source_id": "s1", "normalized_page": 2}]},
                          "manifest": {"items": [{"source_id": "s1", "original_name": "合成教材.pdf"}]}}}


def test_template_uses_per_point_counts_latest_answer_and_source():
    states = [SimpleNamespace(concept_id="real-concept-one", label="快取有效期", attempts=3,
        completed_claim_ids=["p2"], weak_claim_ids=["p1"])]
    plan = {"concepts": [{"concept_id": "real-concept-one", "targets": [
        {"claim_id": "p1", "covered_claim_ids": ["p1"]},
        {"claim_id": "p2", "covered_claim_ids": ["p2"]}]}]}
    events = [SimpleNamespace(target_concept_id="real-concept-one", target_claim_id=claim, is_correct=correct)
              for claim, correct in (("p1", False), ("p2", True), ("p1", True))]
    data = diagnosis_input(document(), states, events, plan, scope="set")
    result = template_diagnosis(data)
    strength = result["strengths"][0]["points"][0]
    assert strength["claim_id"] == "p2" and strength["correct_count"] == 1
    weakness = result["weaknesses"][0]["points"][0]
    assert weakness["claim_id"] == "p1"
    assert weakness["answer_count"] == 2 and weakness["correct_count"] == 1
    assert weakness["latest_is_correct"] is True
    assert weakness["title"] == "freshness_lifetime > current_age 時為新鮮。"
    assert weakness["evidence"] == [{"evidence_id": "e1", "page": 1, "source_id": "s1",
                                    "source_name": "合成教材.pdf", "normalized_page": 2}]
    assert result["recommendations"][0]["concept_id"] == "real-concept-one"
    assert "未測概念" not in str(result)


def test_no_evidence_is_not_a_strength_or_a_weakness():
    assert template_diagnosis({"scope": "material", "concepts": []}) == {
        "strengths": [], "weaknesses": [], "recommendations": []}


def test_short_ids_are_mapped_back_and_no_real_ids_reach_model():
    calls = []
    def model(client, **kwargs):
        calls.append(kwargs["request"])
        return {"c1": {"level": "hard", "reason": "需要區分有效期與目前經過時間。"}, "c2": {"level": "standard", "reason": ""}}
    result = generate_difficulty(document(), _runtime_lock(), semantic_call=model)
    assert len(calls) == 1 and "real-concept" not in str(calls)
    assert result["concepts"][0]["concept_id"] == "real-concept-one"
    assert result["concepts"][0]["level"] == "hard"
    assert result["concepts"][1]["level"] == "standard"


@pytest.mark.parametrize("ids", [["c1"], ["c1", "c9"]])
def test_difficulty_rejects_missing_duplicate_or_foreign_short_id(ids):
    with pytest.raises(ValueError, match="DIFFICULTY_OUTPUT_INVALID"):
        generate_difficulty(document(), _runtime_lock(), semantic_call=lambda *a, **k: {identity: {"level": "standard", "reason": ""} for identity in ids})


def test_call_budget_is_checked_before_any_model_request():
    source = document()
    for concept in source["concepts"]:
        concept["claims"] = [{"text": "word " * 2500}]
    lock = _runtime_lock()
    lock["semantic_service"]["max_model_len"] = 8192
    calls = []
    with pytest.raises(ValueError, match="CALL_BUDGET_EXCEEDED"):
        generate_difficulty(source, lock, max_calls=1, semantic_call=lambda *a, **k: calls.append(k))
    assert calls == []


def test_old_runtime_snapshot_is_valid_without_rewriting_it():
    current = _runtime_lock()
    old = deepcopy(current)
    old.pop("concept_difficulty")
    original = deepcopy(old)
    assert validate_runtime_lock(old) == original
    assert validate_runtime_lock(current) == current
    current["concept_difficulty"]["request_schema"] = "wrong"
    with pytest.raises(Exception, match="RUNTIME_LOCK_INVALID"):
        validate_runtime_lock(current)

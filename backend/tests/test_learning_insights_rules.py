"""診斷來源、結構難度門檻與複習建議排序。"""
from types import SimpleNamespace

from learning_adaptation.learning_insights import diagnosis_input, template_diagnosis, structural_difficulty


def concept_views():
    evidence = {"evidence_id": "e1", "page": 1, "source_id": "s1",
                "normalized_page": 2, "source_name": "合成教材.pdf"}
    return [{"concept_id": "real-concept-one", "label": "快取有效期", "claims": [
        {"claim_id": "p1", "text": "freshness_lifetime > current_age 時為新鮮。", "evidence": [evidence]},
        {"claim_id": "p2", "text": "過期後需要重新驗證。", "evidence": [evidence]}]}]


def test_template_uses_per_point_counts_latest_answer_and_source():
    states = [SimpleNamespace(concept_id="real-concept-one", label="快取有效期", attempts=3, correct_answers=2,
        completed_claim_ids=["p2"], weak_claim_ids=["p1"])]
    plan = {"concepts": [{"concept_id": "real-concept-one", "targets": [
        {"claim_id": "p1", "covered_claim_ids": ["p1"]},
        {"claim_id": "p2", "covered_claim_ids": ["p2"]}]}]}
    events = [SimpleNamespace(target_concept_id="real-concept-one", target_claim_id=claim, is_correct=correct)
              for claim, correct in (("p1", False), ("p2", True), ("p1", True))]
    data = diagnosis_input(concept_views(), states, events, plan, scope="set")
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


def test_no_evidence_is_not_a_strength_or_a_weakness():
    assert template_diagnosis({"scope": "material", "concepts": []}) == {
        "strengths": [], "weaknesses": [], "recommendations": []}



def test_structural_difficulty_uses_longest_prerequisite_chain_and_five_points():
    def concept(identity, prerequisites=(), count=1):
        return {"concept_id": identity, "label": identity, "prerequisite_ids": list(prerequisites),
                "claim_ids": [f"{identity}-point-{index}" for index in range(count)]}
    plan = {"concepts": [concept("deep", ["root", "middle"]),
                         concept("middle", ["first"]), concept("first", ["root"]),
                         concept("root", count=4), concept("many", count=5)]}
    result = {item["concept_id"]: item for item in structural_difficulty(plan)}
    assert len(result) == 5
    assert result["deep"]["level"] == "hard"
    assert "需先學 3 層先備概念" in result["deep"]["reason"]
    assert result["middle"]["level"] == "standard"
    assert result["root"]["level"] == "standard"
    assert result["many"]["level"] == "hard"
    assert result["many"]["reason"] == "包含 5 個重點"


def test_recommendations_prioritize_weak_point_count_then_personal_and_limit_three():
    def concept(identity, weak, personal):
        return {"concept_id": identity, "label": identity, "points": [],
                "completed_claim_ids": [], "weak_claim_ids": [str(index) for index in range(weak)],
                "personal_difficulty": personal, "answer_count": 4, "incorrect_count": 2}
    result = template_diagnosis({"concepts": [concept("two", 2, False), concept("tie-personal", 2, True),
        concept("three", 3, False), concept("only-personal", 0, True), concept("one", 1, True)]})
    assert [item["concept_id"] for item in result["recommendations"]] == ["three", "tie-personal", "two"]
    assert result["recommendations"][0]["reason"] == "3 個重點尚待加強。"
    assert all(set(item) == {"concept_id", "label", "reason"} for item in result["recommendations"])
    personal_only = template_diagnosis({"concepts": [concept("only-personal", 0, True)]})
    assert personal_only["recommendations"] == [
        {"concept_id": "only-personal", "label": "only-personal", "reason": "答錯 2/4 題，是你的難點。"}]

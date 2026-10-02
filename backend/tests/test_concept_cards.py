from copy import deepcopy

import pytest

from concept_card_fixtures import card_structure, LITERAL
from knowledge_map.concept_cards import project_concept_cards
from runtime.storage.knowledge_structures import _view

MATERIAL = "00000000-0000-4000-8000-000000000011"


def test_source_bound_cards_preserve_literals_direction_quality_and_order():
    document = card_structure()
    view = _view(document, MATERIAL)
    before = deepcopy(view)
    ids = [item["concept_id"] for item in view["concepts"]]
    result = project_concept_cards(view, MATERIAL, ids[::-1] + ids)
    assert result == project_concept_cards(view, MATERIAL, ids)
    assert result["selection"]["concept_ids"] == ids
    assert result["selection"]["material_id"] == MATERIAL
    assert result["selection"]["content_material_id"] == document["material_id"] != MATERIAL
    assert result["selection"]["knowledge_structure_revision"] == document["revision"]
    assert len(result["cards"][0]["claims"]) == 2
    assert result["cards"][0]["claims"][1]["text"] == LITERAL
    assert result["status"] == document["status"]
    assert result["status"]["quality"] == "needs_review"
    assert result["excluded_pages"] == document["excluded_pages"]
    assert len(result["relations"]) == 2
    known = {item["evidence_id"] for item in document["evidence"]}
    for card in result["cards"]:
        assert card["claims"] == next(c for c in view["concepts"] if c["concept_id"] == card["concept_id"])["claims"]
        for claim in card["claims"]:
            assert claim["evidence"]
            assert all(e["evidence_id"] in known and e["source_id"] for e in claim["evidence"])
    for projected, canonical in zip(result["relations"], document["relations"]):
        for key in ("relation_id", "source_concept_id", "target_concept_id", "type", "learner_reason", "evidence_refs"):
            assert projected[key] == canonical[key]
        assert [e["evidence_id"] for e in projected["evidence"]] == canonical["evidence_refs"]
    assert view == before


def test_single_and_unrelated_cards_do_not_invent_comparison():
    view = _view(card_structure(), MATERIAL)
    ids = [c["concept_id"] for c in view["concepts"]]
    single = project_concept_cards(view, MATERIAL, ids[:1])
    assert len(single["cards"]) == 1
    assert [r["type"] for r in single["relations"]] == ["prerequisite"]
    unrelated = project_concept_cards(view, MATERIAL, [ids[0], ids[3]])
    assert len(unrelated["cards"]) == 2
    assert not any(r["type"] == "contrast" for r in unrelated["relations"])
    assert project_concept_cards(view, MATERIAL, [ids[3]])["relations"] == []


@pytest.mark.parametrize("ids", [[], ["unknown"]])
def test_invalid_selection_rejected(ids):
    with pytest.raises(ValueError, match="REQUEST_INVALID"):
        project_concept_cards(_view(card_structure(), MATERIAL), MATERIAL, ids)


@pytest.mark.parametrize("field", ["evidence_refs", "source_concept_id"])
def test_dangling_relation_rejected(field):
    view = _view(card_structure(), MATERIAL)
    ids = [c["concept_id"] for c in view["concepts"]]
    view["relations"][0][field] = ["missing"] if field == "evidence_refs" else "missing"
    with pytest.raises(ValueError, match="KNOWLEDGE_STRUCTURE_INVALID"):
        project_concept_cards(view, MATERIAL, ids)

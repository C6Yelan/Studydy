from copy import deepcopy

from knowledge_map.artifacts import (
    build_knowledge_map_view,
    build_review_knowledge_map,
    validate_knowledge_map,
)
from pdf_evidence.artifact_reason_codes import FORMAL_REASON_CODES
from pdf_evidence.study_material_output import build_study_material_output
from pdf_evidence.ocr_page_evidence import canonical_sha256
from test_study_material_output import producer_output


def _reason_lists(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "reason_codes":
                yield item
            else:
                yield from _reason_lists(item)
    elif isinstance(value, list):
        for item in value:
            yield from _reason_lists(item)


def test_map_v2_is_review_only_and_public_view_has_pdf_locators_without_text():
    study_output = build_study_material_output(producer_output())
    knowledge_map = build_review_knowledge_map(study_output)
    view = build_knowledge_map_view(knowledge_map)
    assert knowledge_map["schema"] == "knowledge-map/v2"
    assert (knowledge_map["quality"], knowledge_map["decision"]) == (
        "needs_review",
        "review",
    )
    assert "relations" not in knowledge_map
    assert "learning_path" not in knowledge_map
    assert view["schema"] == "knowledge-map-view/v2"
    evidence = view["concepts"][0]["evidence"][0]
    assert evidence["page_number"] == 1
    assert evidence["region"]["coordinate_space"] == "unrotated_pdf_points"
    assert "text" not in str(view)
    assert "runtime_binding" not in str(view)
    assert all(
        reasons and set(reasons) <= FORMAL_REASON_CODES
        for document in (producer_output(), study_output, knowledge_map, view)
        for reasons in _reason_lists(document)
    )
    assert validate_knowledge_map(knowledge_map, study_output) is None


def test_map_view_fails_when_concept_evidence_is_orphaned():
    knowledge_map = build_review_knowledge_map(
        build_study_material_output(producer_output())
    )
    knowledge_map["concepts"][0]["evidence_ids"] = [
        "evidence:sha256:" + "f" * 64
    ]
    content = {key: value for key, value in knowledge_map.items() if key != "revision"}
    from pdf_evidence.ocr_page_evidence import canonical_sha256

    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(content)
    try:
        build_knowledge_map_view(knowledge_map)
    except ValueError as error:
        assert str(error) == "KNOWLEDGE_MAP_INVALID"
    else:
        raise AssertionError("orphan Evidence must fail closed")


def test_map_revision_tamper_is_rejected():
    knowledge_map = build_review_knowledge_map(
        build_study_material_output(producer_output())
    )
    tampered = deepcopy(knowledge_map)
    tampered["concepts"][0]["label"] = "Changed"
    assert validate_knowledge_map(tampered) == "KNOWLEDGE_MAP_INVALID"


def test_recomputed_map_revision_cannot_hide_nested_unexpected_field():
    knowledge_map = build_review_knowledge_map(
        build_study_material_output(producer_output())
    )
    knowledge_map["concepts"][0]["unexpected_field"] = True
    identity = dict(knowledge_map)
    identity.pop("revision")
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(identity)
    assert validate_knowledge_map(knowledge_map) == "KNOWLEDGE_MAP_INVALID"

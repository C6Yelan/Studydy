from copy import deepcopy

import pytest

from knowledge_map.artifacts import (
    build_knowledge_map,
    build_knowledge_map_view,
    validate_knowledge_map,
)
from knowledge_map.formal_concepts import (
    VERIFICATION_DIAGNOSTIC_FIELDS,
    build_deduplication_request,
    canonicalize_concepts,
)
from knowledge_map.prerequisites import build_prerequisite_constraints
from pdf_evidence.concept_generation import claim_id, concept_id
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.study_material_output import build_study_material_output
from runtime.api.models import KnowledgeMapView
from test_study_material_output import producer_output


def _reidentify_study(study):
    content = {key: value for key, value in study.items() if key != "output_id"}
    study["output_id"] = "study-material-output:sha256:" + canonical_sha256(content)


def _study_with_two_concepts():
    study = build_study_material_output(producer_output())
    first = study["concepts"][0]
    page_ref = first["page_ref"]
    evidence_id = first["claims"][0]["evidence_ids"][0]
    claim = {
        "text": "A second grounded Claim remains independently useful.",
        "evidence_ids": [evidence_id],
    }
    claim = {"claim_id": claim_id(page_ref, claim, index=0), **claim}
    second = {
        **deepcopy(first),
        "label": "Second concept",
        "claims": [claim],
    }
    second["concept_id"] = concept_id(page_ref, second["label"], second["claims"])
    study["concepts"] = sorted([first, second], key=lambda item: item["concept_id"])
    _reidentify_study(study)
    return study


def _verification(decisions):
    diagnostics = {field: 0 for field in VERIFICATION_DIAGNOSTIC_FIELDS}
    diagnostics["qwen_same_pairs"] = sum(
        decision["decision"] == "SAME" for decision in decisions
    )
    diagnostics["qwen_distinct_pairs"] = sum(
        decision["decision"] == "DISTINCT" for decision in decisions
    )
    diagnostics["qwen_uncertain_pairs"] = sum(
        decision["decision"] == "UNCERTAIN" for decision in decisions
    )
    diagnostics["verifier_requested_pairs"] = diagnostics["qwen_same_pairs"]
    diagnostics["verifier_scored_pairs"] = diagnostics["qwen_same_pairs"]
    diagnostics["verifier_allowed_pairs"] = diagnostics["qwen_same_pairs"]
    return diagnostics


def _resolution(study, decision="UNCERTAIN", failure_reason=None):
    request, aliases = build_deduplication_request(study)
    decisions = [
        {"id": pair["id"], "decision": decision}
        for pair in request["pairs"]
    ]
    return canonicalize_concepts(
        study,
        request,
        aliases,
        decisions,
        verification_diagnostics=_verification(decisions),
        failure_reason=failure_reason,
    )


def _map(study, resolution, prerequisite_constraints=None):
    return build_knowledge_map(
        study,
        [resolution],
        material_runtime_binding_sha256="a" * 64,
        prerequisite_constraints=prerequisite_constraints,
    )


def test_uncertainty_and_failure_preserve_every_source_concept():
    study = _study_with_two_concepts()
    resolution = _resolution(study, failure_reason="MODEL_OUTPUT_INVALID")
    assert resolution["processing"] == "partial"
    assert len(resolution["formal_concepts"]) == 2
    assert sorted(
        source_id
        for concept in resolution["formal_concepts"]
        for source_id in concept["source_concept_ids"]
    ) == sorted(concept["concept_id"] for concept in study["concepts"])
    assert all("group_id" not in concept for concept in resolution["formal_concepts"])
    assert "group_id" not in resolution


def test_positive_same_merges_and_preserves_complete_lineage():
    study = _study_with_two_concepts()
    resolution = _resolution(study, decision="SAME")
    assert len(resolution["formal_concepts"]) == 1
    concept = resolution["formal_concepts"][0]
    assert concept["operation"] == "MERGE"
    assert set(concept["source_concept_ids"]) == {
        source["concept_id"] for source in study["concepts"]
    }
    assert len(concept["source_members"]) == 2


def test_tree_and_path_each_place_every_canonical_concept_once():
    study = _study_with_two_concepts()
    knowledge_map = _map(study, _resolution(study))
    view = build_knowledge_map_view(knowledge_map, study)
    tree_ids = [
        concept_id
        for section in knowledge_map["document_tree"]["sections"]
        for concept_id in section["concept_ids"]
    ]
    path_ids = [
        step["formal_concept_id"]
        for step in knowledge_map["initial_learning_path"]
    ]
    canonical_ids = [
        concept["formal_concept_id"] for concept in knowledge_map["formal_concepts"]
    ]
    assert tree_ids == path_ids
    assert set(tree_ids) == set(canonical_ids)
    assert len(tree_ids) == len(set(tree_ids)) == len(canonical_ids)
    assert view["schema"] == "knowledge-map-view/v10"
    assert "relations" not in knowledge_map
    assert "relations" not in view
    assert "topology" not in knowledge_map
    assert "topology" not in view


def test_path_order_is_independent_of_display_connectors():
    study = _study_with_two_concepts()
    knowledge_map = _map(study, _resolution(study))
    assert all(set(step["order_basis"]) == {
        "prerequisite_constraint_ids", "section_id", "page_ref", "page_number",
        "reading_order", "evidence_id"
    } for step in knowledge_map["initial_learning_path"])
    assert all("parent" not in str(step) for step in knowledge_map["initial_learning_path"])


def test_only_positive_constraint_can_change_baseline_path():
    study = _study_with_two_concepts()
    resolution = _resolution(study)
    concepts = sorted(
        resolution["formal_concepts"],
        key=lambda concept: concept["formal_concept_id"],
    )
    baseline = _map(study, resolution)
    source, target = concepts[1], concepts[0]
    claim = source["claims"][0]
    proposal = {
        "proposal_id": "reverse-baseline",
        "source_formal_concept_id": source["formal_concept_id"],
        "target_formal_concept_id": target["formal_concept_id"],
        "evidence_bindings": [{
            "owner_formal_concept_id": source["formal_concept_id"],
            "claim_id": claim["claim_id"],
            "evidence_ids": sorted(claim["evidence_ids"]),
        }],
    }
    constraints, diagnostics = build_prerequisite_constraints(
        [proposal],
        resolution["formal_concepts"],
        {"model_id": "local-nli", "revision": "fixed", "policy": "positive-only/v1"},
        lambda _: True,
    )
    constrained = _map(study, resolution, constraints)
    assert diagnostics["accepted"] == 1
    assert [
        step["formal_concept_id"] for step in constrained["initial_learning_path"]
    ][:2] == [source["formal_concept_id"], target["formal_concept_id"]]
    assert constrained["initial_learning_path"][1]["order_basis"][
        "prerequisite_constraint_ids"
    ] == [constraints[0]["prerequisite_constraint_id"]]
    view = build_knowledge_map_view(constrained, study)
    tree_ids = [
        concept_id
        for section in view["document_tree"]["sections"]
        for concept_id in section["concept_ids"]
    ]
    path_ids = [
        step["formal_concept_id"] for step in view["initial_learning_path"]
    ]
    assert tree_ids != path_ids
    assert KnowledgeMapView.model_validate(view).initial_learning_path
    for mutate in (
        lambda invalid: invalid["initial_learning_path"].pop(),
        lambda invalid: invalid["initial_learning_path"].__setitem__(
            1, deepcopy(invalid["initial_learning_path"][0])
        ),
    ):
        invalid = deepcopy(view)
        mutate(invalid)
        with pytest.raises(ValueError, match="KNOWLEDGE_MAP_VIEW_INVALID"):
            KnowledgeMapView.model_validate(invalid)

    rejected, rejected_diagnostics = build_prerequisite_constraints(
        [proposal],
        resolution["formal_concepts"],
        {"model_id": "local-nli", "revision": "fixed", "policy": "positive-only/v1"},
        lambda _: False,
    )
    assert rejected == []
    assert rejected_diagnostics["not_positive"] == 1
    assert _map(study, resolution, rejected)["initial_learning_path"] == baseline[
        "initial_learning_path"
    ]


def test_cycle_conflict_and_verifier_failure_publish_no_extra_constraint():
    study = _study_with_two_concepts()
    resolution = _resolution(study)
    left, right = resolution["formal_concepts"]
    def proposal(proposal_id, source, target):
        claim = source["claims"][0]
        return {
            "proposal_id": proposal_id,
            "source_formal_concept_id": source["formal_concept_id"],
            "target_formal_concept_id": target["formal_concept_id"],
            "evidence_bindings": [{
                "owner_formal_concept_id": source["formal_concept_id"],
                "claim_id": claim["claim_id"],
                "evidence_ids": sorted(claim["evidence_ids"]),
            }],
        }
    constraints, diagnostics = build_prerequisite_constraints(
        [proposal("a", left, right), proposal("b", right, left)],
        resolution["formal_concepts"],
        {"model_id": "local-nli", "revision": "fixed", "policy": "positive-only/v1"},
        lambda _: True,
    )
    assert len(constraints) == 1
    assert diagnostics["cycle_or_conflict"] == 1
    failed, failed_diagnostics = build_prerequisite_constraints(
        [proposal("timeout", left, right)],
        resolution["formal_concepts"],
        {"model_id": "local-nli", "revision": "fixed", "policy": "positive-only/v1"},
        lambda _: (_ for _ in ()).throw(TimeoutError()),
    )
    assert failed == []
    assert failed_diagnostics["not_positive"] == 1


def test_missing_resources_yields_partial_sidecar_without_blocking_core():
    study = _study_with_two_concepts()
    knowledge_map = _map(study, _resolution(study))
    sidecar = knowledge_map["supplementary_resources"]
    assert sidecar["processing"] == "partial"
    assert sidecar["binding"] is None
    assert all(
        concept["supplementary_resources"] == []
        for concept in knowledge_map["formal_concepts"]
    )
    assert knowledge_map["formal_concepts"]
    assert knowledge_map["document_tree"]["sections"]
    assert knowledge_map["initial_learning_path"]


def test_tree_tamper_is_rejected_even_after_revision_recomputation():
    study = _study_with_two_concepts()
    knowledge_map = _map(study, _resolution(study))
    tampered = deepcopy(knowledge_map)
    tampered["document_tree"]["sections"][0]["concept_ids"].append(
        tampered["formal_concepts"][0]["formal_concept_id"]
    )
    content = {key: value for key, value in tampered.items() if key != "revision"}
    tampered["revision"] = "knowledge-map:sha256:" + canonical_sha256(content)
    assert validate_knowledge_map(tampered, study) == "KNOWLEDGE_MAP_INVALID"


def test_core_provenance_corruption_remains_a_hard_failure():
    study = _study_with_two_concepts()
    knowledge_map = _map(study, _resolution(study))
    tampered = deepcopy(knowledge_map)
    tampered["source_binding"]["producer_output_id"] = "wrong"
    content = {key: value for key, value in tampered.items() if key != "revision"}
    tampered["revision"] = "knowledge-map:sha256:" + canonical_sha256(content)
    assert validate_knowledge_map(tampered, study) == "KNOWLEDGE_MAP_INVALID"

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from pdf_evidence.concept_api import ConceptAPIError
from pdf_evidence.local_ai_process import LocalAIError

import knowledge_map.local_generation as local_generation

from knowledge_map.artifacts import (
    build_knowledge_map,
    build_knowledge_map_view,
    validate_knowledge_map,
)
from knowledge_map.formal_concepts import (
    FormalConceptError,
    build_resolution_requests,
    resolve_singleton,
    validate_resolution,
    validate_resolution_with_isolation,
)
from knowledge_map.relations import (
    MAX_RELATION_PAIRS,
    RelationError,
    _is_safe_prerequisite_claim,
    build_relation_artifact,
    build_relation_request,
    select_relation_pairs,
    validate_relations,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.concept_generation import claim_id, concept_id
from pdf_evidence.study_material_output import build_study_material_output
from learning_resources.map_resources import (
    build_map_resource_context,
    build_resource_library,
    load_bundled_resource_library,
    promote_resources_to_formal_concepts,
)
from test_study_material_output import producer_output


def _study():
    return build_study_material_output(producer_output())


def _keep_resolution(study):
    request, concepts, claims = build_resolution_requests(study["concepts"])[0]
    source = study["concepts"][0]
    claim_aliases = list(claims)
    candidate = {
        "schema": "formal-concept-resolution/v1",
        "group_id": request["group_id"],
        "resolutions": [{
            "operation": "KEEP",
            "source_ids": ["c1"],
            "nodes": [{"label": source["label"], "claim_ids": claim_aliases}],
        }],
    }
    return validate_resolution(
        candidate,
        request=request,
        concept_aliases=concepts,
        claim_aliases=claims,
        source_concepts=study["concepts"],
    )


def _add_second_same_label_concept(study):
    second = deepcopy(study["concepts"][0])
    second["definition"] = deepcopy(second["definition"])
    second["definition"]["text"] = "A second independently grounded definition."
    second["definition"]["claim_id"] = claim_id(
        second["page_ref"],
        "definition",
        {
            "text": second["definition"]["text"],
            "evidence_ids": second["definition"]["evidence_ids"],
        },
    )
    second["key_points"] = deepcopy(second["key_points"])
    second["key_points"][0]["text"] = "A second independently grounded point."
    second["key_points"][0]["claim_id"] = claim_id(
        second["page_ref"],
        "key_point",
        {
            "text": second["key_points"][0]["text"],
            "evidence_ids": second["key_points"][0]["evidence_ids"],
        },
        index=0,
    )
    second["concept_id"] = concept_id(
        second["page_ref"],
        second["label"],
        second["definition"],
        second["key_points"],
    )
    study["concepts"].append(second)
    study.pop("output_id")
    study["output_id"] = "study-material-output:sha256:" + canonical_sha256(study)
    return second


def _resource_promotion(study, formal_concepts):
    library = load_bundled_resource_library()
    context = build_map_resource_context(study, library)
    return promote_resources_to_formal_concepts(
        formal_concepts, context, study, library
    )


def _matching_resource_library(label):
    source_sha = "b" * 64
    return build_resource_library(
        [{
            "source_sha256": source_sha,
            "page_count": 2,
            "title": "Reviewed supplementary notes",
            "authors": ["Ada Student"],
            "source_url": "https://example.edu/notes.pdf",
            "citation": "Ada Student. Reviewed supplementary notes.",
            "license": "CC BY 4.0 International",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "use_boundary": "Attribution required.",
        }],
        [{
            "source_sha256": source_sha,
            "page_number": 2,
            "label": label,
            "evidence": [{
                "quote": "Reviewed evidence for the supplementary concept.",
                "region": {
                    "coordinate_space": "unrotated_pdf_points",
                    "bbox": [20.0, 30.0, 260.0, 55.0],
                },
            }],
        }],
    )


def test_formal_keep_is_claim_grounded_and_exactly_covers_source():
    study = _study()
    artifact = _keep_resolution(study)
    formal = artifact["formal_concepts"][0]
    assert formal["operation"] == "KEEP"
    assert formal["source_concept_ids"] == [study["concepts"][0]["concept_id"]]
    assert [claim["claim_id"] for claim in formal["claims"]] == [
        study["concepts"][0]["definition"]["claim_id"],
        study["concepts"][0]["key_points"][0]["claim_id"],
    ]


def test_singleton_resolution_is_deterministic_keep_with_all_provenance():
    study = _study()
    request, concepts, claims = build_resolution_requests(study["concepts"])[0]

    artifact = resolve_singleton(request, concepts, claims, study["concepts"])
    repeated = resolve_singleton(request, concepts, claims, study["concepts"])

    assert artifact == repeated
    formal = artifact["formal_concepts"][0]
    source = study["concepts"][0]
    assert formal["operation"] == "KEEP"
    assert formal["label"] == source["label"]
    assert formal["source_concept_ids"] == [source["concept_id"]]
    assert formal["source_page_refs"] == [source["page_ref"]]
    assert formal["claims"] == [source["definition"], *source["key_points"]]
    assert {
        claim["claim_id"] for claim in formal["claims"]
    } == set(claims.values())
    assert all(claim["evidence_ids"] for claim in formal["claims"])
    assert formal["operation"] not in {"SPLIT", "MERGE", "RENAME", "DROP"}


def test_singleton_generation_skips_qwen_and_promotes_resource(monkeypatch):
    study = _study()
    library = _matching_resource_library(study["concepts"][0]["label"])
    context = build_map_resource_context(study, library)
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )

    def unexpected_qwen(*_, **__):
        raise AssertionError("singleton must not start or call Qwen")

    monkeypatch.setattr(local_generation, "start_concept_server", unexpected_qwen)
    monkeypatch.setattr(local_generation, "request_structured_text", unexpected_qwen)
    knowledge_map = local_generation.generate_knowledge_map(
        study,
        {
            "runtime_lock": runtime_lock,
            "concept_api_base_url": "http://127.0.0.1:8101",
            "concept_model": runtime_lock["semantic"]["model_id"],
            "concept_max_model_len": 8_192,
        },
        "f" * 64,
        resource_context=context,
        resource_library=library,
    )

    formal = knowledge_map["formal_concepts"][0]
    assert formal["operation"] == "KEEP"
    assert formal["claims"] == [
        study["concepts"][0]["definition"],
        *study["concepts"][0]["key_points"],
    ]
    resource = formal["supplementary_resources"][0]
    assert resource["match_ids"] == [context["matches"][0]["match_id"]]
    assert resource["study_concept_ids"] == [study["concepts"][0]["concept_id"]]
    assert knowledge_map["resource_diagnostics"]["promoted_matches"] == 1


def test_resolution_rejects_claim_subset_as_missing():
    study = _study()
    request, concepts, claims = build_resolution_requests(study["concepts"])[0]
    candidate = {
        "schema": "formal-concept-resolution/v1",
        "group_id": request["group_id"],
        "resolutions": [{
            "operation": "KEEP",
            "source_ids": ["c1"],
            "nodes": [{
                "label": study["concepts"][0]["label"],
                "claim_ids": [next(iter(claims))],
            }],
        }],
    }

    try:
        validate_resolution(
            candidate,
            request=request,
            concept_aliases=concepts,
            claim_aliases=claims,
            source_concepts=study["concepts"],
        )
    except FormalConceptError as error:
        assert str(error) == "RESOLUTION_CLAIM_MISSING"
    else:
        raise AssertionError("claim subset must fail closed")


def test_invalid_merge_shape_keeps_other_grounded_concepts_in_partial_map():
    study = _study()
    sources = []
    for index in range(4):
        concept = deepcopy(study["concepts"][0])
        concept["label"] = "Shared Concept"
        concept["definition"] = deepcopy(concept["definition"])
        concept["definition"]["text"] = f"Concept {index + 1} has a grounded definition."
        concept["definition"]["claim_id"] = claim_id(
            concept["page_ref"], "definition", {
                "text": concept["definition"]["text"],
                "evidence_ids": concept["definition"]["evidence_ids"],
            }
        )
        concept["key_points"] = deepcopy(concept["key_points"])
        concept["key_points"][0]["text"] = f"Concept {index + 1} has a grounded point."
        concept["key_points"][0]["claim_id"] = claim_id(
            concept["page_ref"], "key_point", {
                "text": concept["key_points"][0]["text"],
                "evidence_ids": concept["key_points"][0]["evidence_ids"],
            }, index=0,
        )
        concept["concept_id"] = concept_id(
            concept["page_ref"], concept["label"],
            concept["definition"], concept["key_points"],
        )
        sources.append(concept)
    study["concepts"] = sources
    study.pop("output_id")
    study["output_id"] = "study-material-output:sha256:" + canonical_sha256(study)
    request, concepts, claims = build_resolution_requests(sources)[0]
    aliases_by_candidate = {
        candidate["id"]: [claim["id"] for claim in candidate["claims"]]
        for candidate in request["candidates"]
    }
    candidate = {
        "schema": "formal-concept-resolution/v1",
        "group_id": request["group_id"],
        "resolutions": [
            {
                "operation": "KEEP",
                "source_ids": [alias],
                "nodes": [{
                    "label": request["candidates"][index]["label"],
                    "claim_ids": aliases_by_candidate[alias],
                }],
            }
            for index, alias in enumerate(("c1", "c2"))
        ] + [{
            "operation": "MERGE",
            "source_ids": ["c3", "c4"],
            "nodes": [
                {"label": "Part A", "claim_ids": aliases_by_candidate["c3"]},
                {"label": "Part B", "claim_ids": aliases_by_candidate["c4"]},
            ],
        }],
    }

    artifact = validate_resolution_with_isolation(
        candidate,
        request=request,
        concept_aliases=concepts,
        claim_aliases=claims,
        source_concepts=sources,
    )
    knowledge_map = build_knowledge_map(
        study,
        [artifact],
        [],
        relation_pair_status={
            "processing": "succeeded",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        },
        resource_promotion=_resource_promotion(study, artifact["formal_concepts"]),
        material_runtime_binding_sha256="f" * 64,
    )

    assert artifact["processing"] == "partial"
    assert len(artifact["formal_concepts"]) == 2
    assert "MODEL_OUTPUT_INVALID" in artifact["reason_codes"]
    assert knowledge_map["processing"] == "partial"
    assert knowledge_map["decision"] == "review"
    assert len(knowledge_map["formal_concepts"]) == 2
    assert "MODEL_OUTPUT_INVALID" in knowledge_map["reason_codes"]
    assert validate_knowledge_map(knowledge_map) is None


def test_resolution_rejects_missing_duplicate_and_split_above_two():
    study = _study()
    request, concepts, claims = build_resolution_requests(study["concepts"])[0]
    base = {
        "schema": "formal-concept-resolution/v1",
        "group_id": request["group_id"],
        "resolutions": [],
    }
    for candidate in (
        base,
        {
            **base,
            "resolutions": [
                {
                    "operation": "KEEP",
                    "source_ids": ["c1"],
                    "nodes": [{
                        "label": study["concepts"][0]["label"],
                        "claim_ids": list(claims),
                    }],
                },
                {"operation": "DROP", "source_ids": ["c1"], "nodes": []},
            ],
        },
        {
            **base,
            "resolutions": [{
                "operation": "KEEP",
                "source_ids": ["c1"],
                "nodes": [{
                    "label": study["concepts"][0]["label"],
                    "claim_ids": ["invented"],
                }],
            }],
        },
        {
            **base,
            "resolutions": [{
                "operation": "SPLIT",
                "source_ids": ["c1"],
                "nodes": [
                    {"label": "A", "claim_ids": [list(claims)[0]]},
                    {"label": "B", "claim_ids": [list(claims)[1]]},
                    {"label": "C", "claim_ids": [list(claims)[1]]},
                ],
            }],
        },
    ):
        try:
            validate_resolution(
                candidate,
                request=request,
                concept_aliases=concepts,
                claim_aliases=claims,
                source_concepts=study["concepts"],
            )
        except FormalConceptError:
            pass
        else:
            raise AssertionError("invalid resolution must fail closed")


def test_resolution_accepts_merge_rename_split_and_drop_shapes():
    study = _study()
    source = study["concepts"][0]
    request, concepts, claims = build_resolution_requests([source])[0]
    claim_aliases = list(claims)
    candidates = [
        {
            "operation": "RENAME",
            "source_ids": ["c1"],
            "nodes": [{"label": "Renamed concept", "claim_ids": claim_aliases}],
        },
        {
            "operation": "SPLIT",
            "source_ids": ["c1"],
            "nodes": [
                {"label": "Part A", "claim_ids": [claim_aliases[0]]},
                {"label": "Part B", "claim_ids": [claim_aliases[1]]},
            ],
        },
        {"operation": "DROP", "source_ids": ["c1"], "nodes": []},
    ]
    for resolution in candidates:
        artifact = validate_resolution(
            {
                "schema": "formal-concept-resolution/v1",
                "group_id": request["group_id"],
                "resolutions": [resolution],
            },
            request=request,
            concept_aliases=concepts,
            claim_aliases=claims,
            source_concepts=[source],
        )
        assert len(artifact["formal_concepts"]) == len(resolution["nodes"])

    second = deepcopy(source)
    second["definition"] = deepcopy(source["definition"])
    second["definition"]["text"] = "Second definition"
    second["definition"]["claim_id"] = claim_id(
        second["page_ref"],
        "definition",
        {
            "text": second["definition"]["text"],
            "evidence_ids": second["definition"]["evidence_ids"],
        },
    )
    second["key_points"] = deepcopy(source["key_points"])
    second["key_points"][0]["text"] = "Second point"
    second["key_points"][0]["claim_id"] = claim_id(
        second["page_ref"],
        "key_point",
        {
            "text": second["key_points"][0]["text"],
            "evidence_ids": second["key_points"][0]["evidence_ids"],
        },
        index=0,
    )
    second["concept_id"] = concept_id(
        second["page_ref"],
        second["label"],
        second["definition"],
        second["key_points"],
    )
    request, concepts, claims = build_resolution_requests([source, second])[0]
    artifact = validate_resolution(
        {
            "schema": "formal-concept-resolution/v1",
            "group_id": request["group_id"],
            "resolutions": [{
                "operation": "MERGE",
                "source_ids": ["c2", "c1"],
                "nodes": [{
                    "label": source["label"],
                    "claim_ids": list(reversed(claims)),
                }],
            }],
        },
        request=request,
        concept_aliases=concepts,
        claim_aliases=claims,
        source_concepts=[source, second],
    )
    assert artifact["formal_concepts"][0]["source_concept_ids"] == sorted(
        [source["concept_id"], second["concept_id"]]
    )


def test_relation_aliases_are_formal_and_wrong_evidence_owner_fails():
    study = _study()
    resolution = _keep_resolution(study)
    first = resolution["formal_concepts"][0]
    second = deepcopy(first)
    second["formal_concept_id"] = "formal-concept:sha256:" + "f" * 64
    first["label"] = "Alpha"
    first["claims"][0]["text"] = "Alpha contains Beta."
    second["label"] = "Beta"
    second["claims"][0]["text"] = "Beta is independently grounded."
    second["resolution_order"] = [1, 0]
    formal = [first, second]
    pairs, status = select_relation_pairs(formal, {study["pages"][0]["page_ref"]: 1})
    assert status["processing"] == "succeeded"
    request, concept_aliases, evidence_aliases = build_relation_request(pairs[0], formal)
    pair = request["pairs"][0]
    source_evidence = next(alias for alias, owner in evidence_aliases.items() if owner[0] == concept_aliases[pair["left"]])
    target_evidence = next(
        alias for alias, owner in evidence_aliases.items()
        if owner[0] == concept_aliases[pair["right"]]
    )
    candidate = {
        "schema": "formal-relations/v3",
        "pairs": [{
            "id": pair["id"],
            "outcome": "relations",
            "relations": [{
                "type": "contains",
                "source": pair["left"],
                "target": pair["right"],
                "relation_evidence_ids": [source_evidence],
            }],
        }],
    }
    artifact = validate_relations(
        candidate,
        request=request,
        concept_aliases=concept_aliases,
        evidence_aliases=evidence_aliases,
        formal_concepts=formal,
        evidence_pages={study["evidence_index"][0]["evidence_id"]: study["pages"][0]["page_ref"]},
    )
    assert artifact["relations"][0]["source_formal_concept_id"].startswith("formal-concept:")
    relation_evidence = artifact["relations"][0]["relation_evidence"]
    assert relation_evidence == [{
        "owner_formal_concept_id": first["formal_concept_id"],
        "claim_id": first["claims"][0]["claim_id"],
        "evidence_ids": first["claims"][0]["evidence_ids"],
    }]
    candidate["pairs"][0]["relations"][0]["relation_evidence_ids"] = [
        target_evidence
    ]
    try:
        validate_relations(
            candidate,
            request=request,
            concept_aliases=concept_aliases,
            evidence_aliases=evidence_aliases,
            formal_concepts=formal,
            evidence_pages={study["evidence_index"][0]["evidence_id"]: study["pages"][0]["page_ref"]},
        )
    except RelationError:
        pass
    else:
        raise AssertionError("wrong owner must fail closed")

    candidate["pairs"][0]["relations"][0]["relation_evidence_ids"] = [
        "unresolved"
    ]
    with pytest.raises(RelationError, match="RELATION_EVIDENCE_INVALID"):
        validate_relations(
            candidate,
            request=request,
            concept_aliases=concept_aliases,
            evidence_aliases=evidence_aliases,
            formal_concepts=formal,
            evidence_pages={
                study["evidence_index"][0]["evidence_id"]:
                study["pages"][0]["page_ref"]
            },
        )

    relation = {
        "type": "contains",
        "source": pair["left"],
        "target": pair["right"],
        "relation_evidence_ids": [source_evidence],
    }
    for invalid_relations in (
        [relation, deepcopy(relation)],
        [
            relation,
            {
                "type": "contains",
                "source": pair["right"],
                "target": pair["left"],
                "relation_evidence_ids": [target_evidence],
            },
        ],
        [{**relation, "source": study["concepts"][0]["concept_id"]}],
    ):
        invalid = {
            "schema": "formal-relations/v3",
            "pairs": [{
                "id": pair["id"],
                "outcome": "relations",
                "relations": invalid_relations,
            }],
        }
        try:
            validate_relations(
                invalid,
                request=request,
                concept_aliases=concept_aliases,
                evidence_aliases=evidence_aliases,
                formal_concepts=formal,
                evidence_pages={
                    study["evidence_index"][0]["evidence_id"]:
                    study["pages"][0]["page_ref"]
                },
            )
        except RelationError:
            pass
        else:
            raise AssertionError("duplicate, conflict, or raw endpoint must fail")


def _relation_concept(
    number, label, claim_text, *, page_number=1, group="group:one", evidence_number=None
):
    evidence_number = evidence_number if evidence_number is not None else number + 100
    return {
        "formal_concept_id": f"formal-concept:sha256:{number:064x}",
        "group_id": group,
        "label": label,
        "claims": [{
            "claim_id": f"claim:sha256:{number:064x}",
            "text": claim_text,
            "evidence_ids": [f"evidence:sha256:{evidence_number:064x}"],
        }],
        "source_page_refs": [f"page:sha256:{page_number:064x}"],
        "resolution_order": [number, 0],
    }


def _relation_pages(concepts):
    return {
        evidence_id: concept["source_page_refs"][0]
        for concept in concepts
        for claim in concept["claims"]
        for evidence_id in claim["evidence_ids"]
    }


def _add_relation_claim(concept, number, text, evidence_number):
    evidence_id = f"evidence:sha256:{evidence_number:064x}"
    concept["claims"].append(
        {
            "claim_id": f"claim:sha256:{number:064x}",
            "text": text,
            "evidence_ids": [evidence_id],
        }
    )
    return evidence_id


def test_relation_selector_prioritizes_explicit_cross_page_pair_over_adjacency():
    concepts = [
        _relation_concept(1, "Algebra", "Algebra contains Eigenvectors.", page_number=1),
        _relation_concept(2, "Numbers", "Numbers are quantities.", page_number=1),
        _relation_concept(3, "Shapes", "Shapes have boundaries.", page_number=2),
        _relation_concept(4, "Eigenvectors", "Eigenvectors have a direction.", page_number=9),
    ]
    page_numbers = {
        concept["source_page_refs"][0]: index
        for index, concept in enumerate(concepts, start=1)
    }

    batches, status = select_relation_pairs(concepts, page_numbers, ceiling=1)

    assert batches == [[(
        concepts[0]["formal_concept_id"], concepts[3]["formal_concept_id"]
    )]]
    assert status["processing"] == "partial"
    assert status["diagnostics"]["candidate_pairs"] > 1
    assert status["diagnostics"]["selected_signal_counts"]["explicit_relation"] == 1


def test_relation_selector_keeps_fixed_ceiling_deterministic_and_partial():
    concepts = [
        _relation_concept(
            number,
            f"Concept {number}",
            f"Concept {number} is grounded.",
            page_number=number,
            group="group:shared",
        )
        for number in range(1, 19)
    ]
    page_numbers = {
        concept["source_page_refs"][0]: number
        for number, concept in enumerate(concepts, start=1)
    }

    first_batches, first_status = select_relation_pairs(concepts, page_numbers)
    second_batches, second_status = select_relation_pairs(
        list(reversed(concepts)), page_numbers
    )

    assert first_batches == second_batches
    assert first_status == second_status
    assert sum(map(len, first_batches)) == MAX_RELATION_PAIRS == 128
    assert all(len(batch) == 16 for batch in first_batches)
    assert first_status == {
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["RELATION_PAIR_CEILING_EXCEEDED"],
        "diagnostics": {
            "possible_pairs": 153,
            "candidate_pairs": 153,
            "selected_pairs": 128,
            "selected_signal_counts": {"adjacent": 11, "same_group": 128},
        },
    }


def test_relation_evidence_gate_skips_verifier_and_edge_without_pair_evidence():
    concepts = [
        _relation_concept(1, "Limits", "Limits describe approaching values."),
        _relation_concept(2, "Derivatives", "Derivatives describe rates of change."),
    ]
    calls = []

    artifact = build_relation_artifact(
        [(concepts[0]["formal_concept_id"], concepts[1]["formal_concept_id"])],
        concepts,
        _relation_pages(concepts),
        lambda *arguments: calls.append(arguments) or True,
    )

    assert artifact["relations"] == []
    assert calls == []
    assert artifact["diagnostics"]["rejected_no_evidence"] == 1
    assert artifact["diagnostics"].get("verifier_calls", 0) == 0


def test_grounded_endpoint_comparison_publishes_related_without_verifier():
    left = _relation_concept(1, "Arrays", "Arrays are compared with Matrices.")
    right = _relation_concept(2, "Matrices", "Matrices store values.")
    calls = []

    artifact = build_relation_artifact(
        [(left["formal_concept_id"], right["formal_concept_id"])],
        [left, right],
        _relation_pages([left, right]),
        lambda *arguments: calls.append(arguments) or True,
    )

    assert [relation["type"] for relation in artifact["relations"]] == ["related"]
    assert artifact["diagnostics"].get("structural_proposals", 0) == 0
    assert calls == []


def test_ambiguous_chinese_by_phrase_does_not_invent_contains_direction():
    left = _relation_concept(
        1, "項目甲", "項目甲由研究團隊篩選出的類型乙。"
    )
    right = _relation_concept(2, "類型乙", "類型乙是獨立分類。")
    calls = []

    artifact = build_relation_artifact(
        [(left["formal_concept_id"], right["formal_concept_id"])],
        [left, right],
        _relation_pages([left, right]),
        lambda *arguments: calls.append(arguments) or True,
    )

    assert [relation["type"] for relation in artifact["relations"]] == ["related"]
    assert artifact["diagnostics"].get("structural_proposals", 0) == 0
    assert calls == []


@pytest.mark.parametrize("supporting_endpoint", ["source", "target"])
def test_single_grounded_contains_statement_reaches_verifier_with_true_owner(
    supporting_endpoint,
):
    parent = _relation_concept(
        1, "Linear algebra", "Linear algebra is a field of study."
    )
    child = _relation_concept(2, "Matrices", "Matrices are rectangular arrays.")
    if supporting_endpoint == "source":
        _add_relation_claim(
            parent, 201, "Linear algebra contains Matrices.", 301
        )
    else:
        _add_relation_claim(
            child, 202, "Matrices is a component of Linear algebra.", 302
        )
    calls = []

    artifact = build_relation_artifact(
        [(parent["formal_concept_id"], child["formal_concept_id"])],
        [parent, child],
        _relation_pages([parent, child]),
        lambda *arguments: calls.append(arguments) or True,
    )

    relation = artifact["relations"][0]
    expected_owner = parent if supporting_endpoint == "source" else child
    assert calls == [("contains", parent, child)]
    assert relation["source_formal_concept_id"] == parent["formal_concept_id"]
    assert relation["target_formal_concept_id"] == child["formal_concept_id"]
    assert relation["relation_evidence"] == [{
        "owner_formal_concept_id": expected_owner["formal_concept_id"],
        "claim_id": expected_owner["claims"][-1]["claim_id"],
        "evidence_ids": expected_owner["claims"][-1]["evidence_ids"],
    }]
    assert artifact["diagnostics"]["contains_proposals"] == 1
    assert artifact["diagnostics"]["structural_proposals"] == 1
    assert artifact["diagnostics"]["verifier_calls"] == 1
    assert artifact["diagnostics"]["verifier_accepted"] == 1


def test_contains_direction_is_deterministic_and_conflict_fails_closed():
    parent = _relation_concept(
        1, "Linear algebra", "Linear algebra is a field of study."
    )
    child = _relation_concept(2, "Matrices", "Matrices are rectangular arrays.")
    parent_support = _add_relation_claim(
        parent, 201, "Linear algebra contains Matrices.", 301
    )
    child_support = _add_relation_claim(
        child, 202, "Matrices is a component of Linear algebra.", 302
    )
    verified = []
    artifact = build_relation_artifact(
        [(parent["formal_concept_id"], child["formal_concept_id"])],
        [parent, child],
        _relation_pages([parent, child]),
        lambda relation_type, source, target: verified.append(
            (relation_type, source["label"], target["label"])
        ) or True,
    )
    relation = artifact["relations"][0]
    assert verified == [("contains", "Linear algebra", "Matrices")]
    assert relation["source_formal_concept_id"] == parent["formal_concept_id"]
    assert relation["target_formal_concept_id"] == child["formal_concept_id"]
    assert relation["relation_evidence"] == [
        {
            "owner_formal_concept_id": parent["formal_concept_id"],
            "claim_id": parent["claims"][-1]["claim_id"],
            "evidence_ids": [parent_support],
        },
        {
            "owner_formal_concept_id": child["formal_concept_id"],
            "claim_id": child["claims"][-1]["claim_id"],
            "evidence_ids": [child_support],
        },
    ]
    relation_evidence_ids = {
        evidence_id
        for item in relation["relation_evidence"]
        for evidence_id in item["evidence_ids"]
    }
    assert parent["claims"][0]["evidence_ids"][0] not in relation_evidence_ids
    assert child["claims"][0]["evidence_ids"][0] not in relation_evidence_ids

    unsupported = build_relation_artifact(
        [(parent["formal_concept_id"], child["formal_concept_id"])],
        [parent, child],
        _relation_pages([parent, child]),
        None,
    )
    assert unsupported["relations"] == []
    assert unsupported["processing"] == "partial"
    assert unsupported["reason_codes"] == ["RELATION_VERIFIER_UNAVAILABLE"]

    child["claims"][-1]["text"] = "Matrices contains Linear algebra."
    conflict = build_relation_artifact(
        [(parent["formal_concept_id"], child["formal_concept_id"])],
        [parent, child],
        _relation_pages([parent, child]),
        lambda *_: True,
    )
    assert conflict["relations"] == []
    assert conflict["diagnostics"]["direction_conflicts"] == 1


def test_structural_proposal_rejected_by_verifier_stays_fail_closed():
    parent = _relation_concept(
        1, "Linear algebra", "Linear algebra contains Matrices."
    )
    child = _relation_concept(2, "Matrices", "Matrices are rectangular arrays.")
    calls = []

    artifact = build_relation_artifact(
        [(parent["formal_concept_id"], child["formal_concept_id"])],
        [parent, child],
        _relation_pages([parent, child]),
        lambda *arguments: calls.append(arguments) or False,
    )

    assert artifact["relations"] == []
    assert calls == [("contains", parent, child)]
    assert artifact["diagnostics"]["structural_proposals"] == 1
    assert artifact["diagnostics"]["verifier_calls"] == 1
    assert artifact["diagnostics"].get("verifier_accepted", 0) == 0
    assert artifact["diagnostics"]["verifier_rejected"] == 1


@pytest.mark.parametrize(
    "reason_code",
    [
        "RELATION_VERIFIER_DEPENDENCY_MISSING",
        "RELATION_VERIFIER_CUDA_UNAVAILABLE",
        "RELATION_VERIFIER_MODEL_LOAD_FAILED",
    ],
)
def test_relation_startup_failure_keeps_related_and_drops_structural(
    monkeypatch, reason_code
):
    parent = _relation_concept(
        1, "Linear algebra", "Linear algebra is a field of study."
    )
    child = _relation_concept(2, "Matrices", "Matrices are rectangular arrays.")
    _add_relation_claim(parent, 201, "Linear algebra contains Matrices.", 301)
    _add_relation_claim(
        child, 202, "Matrices is a component of Linear algebra.", 302
    )
    related_left = _relation_concept(3, "Graphs", "See Networks for related topics.")
    related_right = _relation_concept(4, "Networks", "See Graphs for related topics.")
    concepts = [parent, child, related_left, related_right]
    batches = [[
        (parent["formal_concept_id"], child["formal_concept_id"]),
        (related_left["formal_concept_id"], related_right["formal_concept_id"]),
    ]]

    def fail_start(*_args):
        raise LocalAIError(reason_code)

    monkeypatch.setattr(local_generation, "start_relation_process", fail_start)
    monkeypatch.setattr(
        local_generation,
        "request_structured_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Qwen must not replace the relation verifier")
        ),
    )
    artifacts = local_generation._build_relation_artifacts(
        batches,
        concepts,
        _relation_pages(concepts),
        {"runtime_lock": {"relation_verifier": {"timeout_seconds": 1}}},
    )

    assert [relation["type"] for relation in artifacts[0]["relations"]] == [
        "related"
    ]
    assert artifacts[0]["processing"] == "partial"
    assert artifacts[0]["reason_codes"] == [reason_code]
    assert artifacts[0]["diagnostics"]["verifier_unsupported"] == 1


@pytest.mark.parametrize(
    ("request_outcome", "expected_reason"),
    [
        ("timeout", "RELATION_VERIFIER_TIMEOUT"),
        ("invalid", "RELATION_VERIFIER_RESPONSE_INVALID"),
    ],
)
def test_relation_runtime_failure_rebuilds_all_batches_fail_closed(
    monkeypatch, request_outcome, expected_reason
):
    parent = _relation_concept(
        1, "Linear algebra", "Linear algebra is a field of study."
    )
    child = _relation_concept(2, "Matrices", "Matrices are rectangular arrays.")
    _add_relation_claim(parent, 201, "Linear algebra contains Matrices.", 301)
    _add_relation_claim(
        child, 202, "Matrices is a component of Linear algebra.", 302
    )
    process_events = []

    class Process:
        def request(self, *_args):
            if request_outcome == "timeout":
                raise LocalAIError("CHILD_TIMEOUT")
            return {"schema": "wrong"}

        def close(self):
            process_events.append("close")

        def abort(self):
            process_events.append("abort")

    monkeypatch.setattr(
        local_generation, "start_relation_process", lambda *_args: Process()
    )
    artifacts = local_generation._build_relation_artifacts(
        [[(parent["formal_concept_id"], child["formal_concept_id"])]],
        [parent, child],
        _relation_pages([parent, child]),
        {"runtime_lock": {"relation_verifier": {"timeout_seconds": 1}}},
    )

    assert artifacts[0]["relations"] == []
    assert artifacts[0]["processing"] == "partial"
    assert artifacts[0]["reason_codes"] == [expected_reason]
    assert process_events == ["abort"]


def test_prerequisite_requires_explicit_dependency_not_document_order():
    prerequisite = _relation_concept(1, "Limits", "Limits describe approaching values.")
    target = _relation_concept(2, "Derivatives", "Derivatives describe rates of change.")
    pair = [(prerequisite["formal_concept_id"], target["formal_concept_id"])]
    assert build_relation_artifact(
        pair, [prerequisite, target], _relation_pages([prerequisite, target]), lambda *_: True
    )["relations"] == []

    evidence_id = _add_relation_claim(
        target, 201, "Derivatives requires Limits.", 301
    )
    calls = []
    relation = build_relation_artifact(
        pair,
        [prerequisite, target],
        _relation_pages([prerequisite, target]),
        lambda *arguments: calls.append(arguments) or True,
    )
    accepted = relation["relations"][0]
    assert calls == [("prerequisite", prerequisite, target)]
    assert relation["diagnostics"]["prerequisite_proposals"] == 1
    assert relation["diagnostics"]["verifier_calls"] == 1
    assert relation["diagnostics"]["verifier_accepted"] == 1
    relation = accepted
    assert relation["type"] == "prerequisite"
    assert relation["source_formal_concept_id"] == prerequisite["formal_concept_id"]
    assert relation["relation_evidence"] == [{
        "owner_formal_concept_id": target["formal_concept_id"],
        "claim_id": target["claims"][-1]["claim_id"],
        "evidence_ids": [evidence_id],
    }]

    _add_relation_claim(
        prerequisite, 202, "Limits requires Derivatives.", 302
    )
    assert build_relation_artifact(
        pair, [prerequisite, target], _relation_pages([prerequisite, target]), lambda *_: True
    )["relations"] == []


def test_prerequisite_quality_benchmark_has_safe_direction_and_zero_false_positives():
    fixture_path = (
        Path(__file__).parent
        / "runtime"
        / "fixtures"
        / "prerequisite-quality-v1.json"
    )
    benchmark = json.loads(fixture_path.read_text(encoding="utf-8"))
    counts = {
        "positive_cases": 0,
        "positive_chinese_cases": 0,
        "detected_chinese_positives": 0,
        "detected_positives": 0,
        "false_negatives": 0,
        "negative_cases": 0,
        "false_positives": 0,
        "direction_errors": 0,
        "endpoint_errors": 0,
        "evidence_ownership_errors": 0,
        "verifier_rejects": 0,
        "cycle_rejects": 0,
    }

    for number, (source_label, target_label, claim_text) in enumerate(
        benchmark["positive_explicit"], start=1
    ):
        source = _relation_concept(number * 2, source_label, f"{source_label} is grounded.")
        target = _relation_concept(number * 2 + 1, target_label, claim_text)
        pair = [(source["formal_concept_id"], target["formal_concept_id"])]
        artifact = build_relation_artifact(
            pair, [source, target], _relation_pages([source, target]), lambda *_: True
        )
        counts["positive_cases"] += 1
        is_chinese_case = source_label == "代數"
        counts["positive_chinese_cases"] += is_chinese_case
        if not artifact["relations"]:
            counts["false_negatives"] += 1
            continue
        counts["detected_positives"] += 1
        counts["detected_chinese_positives"] += is_chinese_case
        relation = artifact["relations"][0]
        if relation["source_formal_concept_id"] != source["formal_concept_id"]:
            counts["direction_errors"] += 1
        if relation["target_formal_concept_id"] != target["formal_concept_id"]:
            counts["endpoint_errors"] += 1
        if relation["relation_evidence"][0]["owner_formal_concept_id"] != target[
            "formal_concept_id"
        ]:
            counts["evidence_ownership_errors"] += 1

    negative_cases = (
        benchmark["negative_adjacent"]
        + benchmark["negative_non_learning"]
        + benchmark["negative_chinese"]
    )
    for number, (source_label, target_label, claim_text) in enumerate(
        negative_cases, start=100
    ):
        source = _relation_concept(number * 2, source_label, claim_text)
        target = _relation_concept(
            number * 2 + 1, target_label, f"{target_label} is grounded."
        )
        pair = [(source["formal_concept_id"], target["formal_concept_id"])]
        artifact = build_relation_artifact(
            pair, [source, target], _relation_pages([source, target]), lambda *_: True
        )
        counts["negative_cases"] += 1
        counts["false_positives"] += sum(
            relation["type"] == "prerequisite" for relation in artifact["relations"]
        )

    assert counts == {
        "positive_cases": 24,
        "positive_chinese_cases": 10,
        "detected_chinese_positives": 10,
        "detected_positives": 24,
        "false_negatives": 0,
        "negative_cases": 23,
        "false_positives": 0,
        "direction_errors": 0,
        "endpoint_errors": 0,
        "evidence_ownership_errors": 0,
        "verifier_rejects": 0,
        "cycle_rejects": 0,
    }

    assert all(
        not _is_safe_prerequisite_claim(
            claim_text, reject_non_learning_context=False
        )
        for _, _, claim_text in benchmark["negative_chinese"]
    )


def test_prerequisite_verifier_rejection_never_publishes():
    source = _relation_concept(1, "Algebra", "Algebra is grounded.")
    target = _relation_concept(2, "Calculus", "Calculus requires Algebra.")
    artifact = build_relation_artifact(
        [(source["formal_concept_id"], target["formal_concept_id"])],
        [source, target],
        _relation_pages([source, target]),
        lambda *_: False,
    )

    assert artifact["relations"] == []
    assert artifact["diagnostics"]["verifier_rejected"] == 1


def test_relation_selector_always_ranks_explicit_prerequisite_before_ceiling():
    concepts = [
        _relation_concept(
            number,
            f"Concept {number}",
            f"Concept {number} is grounded.",
            page_number=number,
            group="group:shared",
        )
        for number in range(1, 19)
    ]
    concepts[-1]["claims"][0]["text"] = "Concept 18 requires Concept 1."
    page_numbers = {
        concept["source_page_refs"][0]: number
        for number, concept in enumerate(concepts, start=1)
    }

    batches, _ = select_relation_pairs(concepts, page_numbers, ceiling=1)

    assert batches == [[(
        concepts[0]["formal_concept_id"], concepts[-1]["formal_concept_id"]
    )]]


def test_related_requires_grounded_association_and_never_calls_verifier():
    left = _relation_concept(1, "Graph Model", "Graph models represent entities.")
    right = _relation_concept(2, "Graph Models", "Graph models can be visualized.")
    pair = [(left["formal_concept_id"], right["formal_concept_id"])]
    calls = []
    initial = build_relation_artifact(
        pair, [left, right], _relation_pages([left, right]), lambda *args: calls.append(args) or True
    )
    assert [relation["type"] for relation in initial["relations"]] == ["related"]

    left["claims"][0]["text"] = "See Graph Models for a visualization example."
    right["claims"][0]["text"] = "Visualizations can show entities."
    one_sided = build_relation_artifact(
        pair, [left, right], _relation_pages([left, right]), lambda *_: True
    )
    assert [relation["type"] for relation in one_sided["relations"]] == ["related"]
    assert one_sided["relations"][0]["relation_evidence"] == [{
        "owner_formal_concept_id": left["formal_concept_id"],
        "claim_id": left["claims"][0]["claim_id"],
        "evidence_ids": left["claims"][0]["evidence_ids"],
    }]
    right["claims"][0]["text"] = "See Graph Model for the matching explanation."
    related = build_relation_artifact(
        pair, [left, right], _relation_pages([left, right]), lambda *args: calls.append(args) or True
    )
    assert [relation["type"] for relation in related["relations"]] == ["related"]
    assert calls == []


def test_same_label_duplicate_concepts_do_not_publish_related_edge():
    left = _relation_concept(
        1, "Circular Queue", "Circular Queue is compared with Circular Queue."
    )
    right = _relation_concept(
        2, "Circular Queue", "Circular Queue stores elements in a ring."
    )
    calls = []

    artifact = build_relation_artifact(
        [(left["formal_concept_id"], right["formal_concept_id"])],
        [left, right],
        _relation_pages([left, right]),
        lambda *arguments: calls.append(arguments) or True,
    )

    assert artifact["relations"] == []
    assert artifact["diagnostics"]["rejected_no_evidence"] == 1
    assert calls == []


def test_grounded_plural_endpoint_mention_publishes_related():
    tools = _relation_concept(
        1,
        "Process Description Tools",
        "Process description tools include structured English, decision tables, and decision trees.",
    )
    decision_table = _relation_concept(
        2,
        "Decision Table",
        "A decision table shows combinations of conditions and outcomes.",
    )

    artifact = build_relation_artifact(
        [(tools["formal_concept_id"], decision_table["formal_concept_id"])],
        [tools, decision_table],
        _relation_pages([tools, decision_table]),
        lambda *_: True,
    )

    assert [relation["type"] for relation in artifact["relations"]] == ["related"]
    assert artifact["relations"][0]["relation_evidence"] == [{
        "owner_formal_concept_id": tools["formal_concept_id"],
        "claim_id": tools["claims"][0]["claim_id"],
        "evidence_ids": tools["claims"][0]["evidence_ids"],
    }]


def test_map_revision_binds_formal_nodes_relations_path_and_cycle_exclusion():
    study = _study()
    first = _keep_resolution(study)["formal_concepts"][0]
    nodes = []
    for index in range(3):
        node = deepcopy(first)
        node["source_concept_ids"] = ["concept:sha256:" + str(index + 1) * 64]
        node["resolution_order"] = [index, 0]
        node["claims"] = [deepcopy(first["claims"][0])]
        node["claims"][0]["claim_id"] = "claim:sha256:" + str(index + 1) * 64
        node["claims"][0]["text"] = f"Cycle concept {index + 1}"
        formal_identity = {
            "group_id": node["group_id"],
            "operation": node["operation"],
            "source_concept_ids": node["source_concept_ids"],
            "label": node["label"],
            "claims": node["claims"],
        }
        node["formal_concept_id"] = (
            "formal-concept:sha256:" + canonical_sha256(formal_identity)
        )
        nodes.append(node)
    relations = []
    for source, target in ((0, 1), (1, 2), (2, 0)):
        identity = {
            "type": "prerequisite",
            "source_formal_concept_id": nodes[source]["formal_concept_id"],
            "target_formal_concept_id": nodes[target]["formal_concept_id"],
            "relation_evidence": [{
                "owner_formal_concept_id": nodes[source]["formal_concept_id"],
                "claim_id": nodes[source]["claims"][0]["claim_id"],
                "evidence_ids": [study["evidence_index"][0]["evidence_id"]],
            }],
        }
        relations.append({
            "relation_id": "formal-relation:sha256:" + canonical_sha256(identity),
            **identity,
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        })
    knowledge_map = build_knowledge_map(
        study,
        [{"formal_concepts": nodes}],
        [{"relations": relations, "processing": "succeeded"}],
        relation_pair_status={
            "processing": "succeeded",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        },
        resource_promotion=_resource_promotion(study, nodes),
        material_runtime_binding_sha256="f" * 64,
    )
    assert all(relation["is_in_prerequisite_cycle"] for relation in knowledge_map["relations"])
    assert knowledge_map["initial_learning_path"] == [node["formal_concept_id"] for node in nodes]
    assert validate_knowledge_map(knowledge_map) is None
    view = build_knowledge_map_view(knowledge_map)
    assert view["schema"] == "knowledge-map-view/v6"
    assert view["relations"][0]["source_formal_concept_id"].startswith("formal-concept:")

    tampered = deepcopy(knowledge_map)
    tampered["initial_learning_path"].reverse()
    assert validate_knowledge_map(tampered) == "KNOWLEDGE_MAP_INVALID"

    tampered = deepcopy(knowledge_map)
    relation = tampered["relations"][0]
    relation["relation_evidence"][0]["claim_id"] = "claim:sha256:" + "0" * 64
    relation_identity = {
        key: relation[key]
        for key in (
            "type", "source_formal_concept_id", "target_formal_concept_id",
            "relation_evidence",
        )
    }
    relation["relation_id"] = (
        "formal-relation:sha256:" + canonical_sha256(relation_identity)
    )
    identity = dict(tampered)
    identity.pop("revision")
    tampered["revision"] = "knowledge-map:sha256:" + canonical_sha256(identity)
    assert validate_knowledge_map(tampered) == "KNOWLEDGE_MAP_INVALID"


def test_map_allows_identical_shared_claim_and_rejects_claim_conflicts():
    study = _study()
    first = _keep_resolution(study)["formal_concepts"][0]
    second = deepcopy(first)
    second["label"] = "Related concept"
    second["resolution_order"] = [1, 0]
    second["formal_concept_id"] = "formal-concept:sha256:" + canonical_sha256(
        {
            "group_id": second["group_id"],
            "operation": second["operation"],
            "source_concept_ids": second["source_concept_ids"],
            "label": second["label"],
            "claims": second["claims"],
        }
    )
    knowledge_map = build_knowledge_map(
        study,
        [{"formal_concepts": [first, second]}],
        [],
        relation_pair_status={
            "processing": "succeeded",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        },
        resource_promotion=_resource_promotion(study, [first, second]),
        material_runtime_binding_sha256="f" * 64,
    )
    shared_claim_id = first["claims"][0]["claim_id"]
    assert sum(
        claim["claim_id"] == shared_claim_id
        for concept in knowledge_map["formal_concepts"]
        for claim in concept["claims"]
    ) == 2
    assert validate_knowledge_map(knowledge_map) is None

    for conflict in ("text", "evidence_ids"):
        tampered = deepcopy(knowledge_map)
        conflicting_concept = tampered["formal_concepts"][1]
        original_formal_id = conflicting_concept["formal_concept_id"]
        if conflict == "text":
            conflicting_concept["claims"][0]["text"] = "Conflicting text"
        else:
            alternate_evidence = deepcopy(tampered["evidence_index"][0])
            alternate_evidence["evidence_id"] = "evidence:sha256:" + "9" * 64
            tampered["evidence_index"].append(alternate_evidence)
            conflicting_concept["claims"][0]["evidence_ids"] = [
                alternate_evidence["evidence_id"]
            ]
        conflicting_concept["formal_concept_id"] = (
            "formal-concept:sha256:"
            + canonical_sha256(
                {
                    "group_id": conflicting_concept["group_id"],
                    "operation": conflicting_concept["operation"],
                    "source_concept_ids": conflicting_concept["source_concept_ids"],
                    "label": conflicting_concept["label"],
                    "claims": conflicting_concept["claims"],
                }
            )
        )
        tampered["initial_learning_path"] = [
            conflicting_concept["formal_concept_id"]
            if formal_id == original_formal_id
            else formal_id
            for formal_id in tampered["initial_learning_path"]
        ]
        revision_input = dict(tampered)
        revision_input.pop("revision")
        tampered["revision"] = (
            "knowledge-map:sha256:" + canonical_sha256(revision_input)
        )
        assert validate_knowledge_map(tampered) == "KNOWLEDGE_MAP_INVALID"


def test_zero_formal_concepts_stops_with_partial_reject():
    study = _study()
    knowledge_map = build_knowledge_map(
        study,
        [],
        [],
        relation_pair_status={"processing": "partial", "reason_codes": ["NO_FORMAL_CONCEPT"]},
        resource_promotion=_resource_promotion(study, []),
        material_runtime_binding_sha256="f" * 64,
    )
    assert knowledge_map["formal_concepts"] == []
    assert (knowledge_map["processing"], knowledge_map["decision"]) == ("partial", "reject")
    assert knowledge_map["initial_learning_path"] == []


def test_recomputed_revision_cannot_hide_nested_unexpected_field():
    study = _study()
    knowledge_map = build_knowledge_map(
        study,
        [_keep_resolution(study)],
        [],
        relation_pair_status={"processing": "succeeded", "reason_codes": ["RELATION_REVIEW_REQUIRED"]},
        resource_promotion=_resource_promotion(
            study, _keep_resolution(study)["formal_concepts"]
        ),
        material_runtime_binding_sha256="f" * 64,
    )
    knowledge_map["formal_concepts"][0]["unexpected"] = True
    identity = dict(knowledge_map)
    identity.pop("revision")
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(identity)
    assert validate_knowledge_map(knowledge_map) == "KNOWLEDGE_MAP_INVALID"


def test_map_promotes_resource_with_provenance_and_rejects_tampering():
    study = _study()
    resolution = _keep_resolution(study)
    formal = resolution["formal_concepts"]
    library = _matching_resource_library(study["concepts"][0]["label"])
    context = build_map_resource_context(study, library)
    promotion = promote_resources_to_formal_concepts(formal, context, study, library)

    knowledge_map = build_knowledge_map(
        study,
        [resolution],
        [],
        relation_pair_status={
            "processing": "succeeded",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        },
        resource_promotion=promotion,
        material_runtime_binding_sha256="f" * 64,
    )

    resource = knowledge_map["formal_concepts"][0]["supplementary_resources"][0]
    assert resource["match_ids"] == [context["matches"][0]["match_id"]]
    assert resource["study_concept_ids"] == [study["concepts"][0]["concept_id"]]
    assert knowledge_map["resource_diagnostics"]["promoted_resources"] == 1
    assert build_knowledge_map_view(knowledge_map)["concepts"][0][
        "supplementary_resources"
    ] == [resource]

    tampered = deepcopy(knowledge_map)
    tampered["formal_concepts"][0]["supplementary_resources"][0]["match_ids"] = [
        "resource-match:sha256:" + "0" * 64
    ]
    identity = dict(tampered)
    identity.pop("revision")
    tampered["revision"] = "knowledge-map:sha256:" + canonical_sha256(identity)
    assert validate_knowledge_map(tampered) == "KNOWLEDGE_MAP_INVALID"


def test_map_split_resource_is_review_only_and_not_duplicated():
    study = _study()
    request, concepts, claims = build_resolution_requests(study["concepts"])[0]
    claim_aliases = list(claims)
    resolution = validate_resolution(
        {
            "schema": "formal-concept-resolution/v1",
            "group_id": request["group_id"],
            "resolutions": [{
                "operation": "SPLIT",
                "source_ids": ["c1"],
                "nodes": [
                    {"label": "Part A", "claim_ids": [claim_aliases[0]]},
                    {"label": "Part B", "claim_ids": [claim_aliases[1]]},
                ],
            }],
        },
        request=request,
        concept_aliases=concepts,
        claim_aliases=claims,
        source_concepts=study["concepts"],
    )
    library = _matching_resource_library(study["concepts"][0]["label"])
    context = build_map_resource_context(study, library)
    promotion = promote_resources_to_formal_concepts(
        resolution["formal_concepts"], context, study, library
    )

    knowledge_map = build_knowledge_map(
        study,
        [resolution],
        [],
        relation_pair_status={
            "processing": "succeeded",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        },
        resource_promotion=promotion,
        material_runtime_binding_sha256="f" * 64,
    )

    assert all(
        not concept["supplementary_resources"]
        for concept in knowledge_map["formal_concepts"]
    )
    assert knowledge_map["resource_diagnostics"]["split_review_matches"] == 1
    assert knowledge_map["resource_decisions"][0]["decision"] == "review"
    assert knowledge_map["processing"] == "partial"
    assert "RESOURCE_SPLIT_REVIEW_REQUIRED" in knowledge_map["reason_codes"]
    assert validate_knowledge_map(knowledge_map) is None


def test_knowledge_generation_retries_only_a_temporary_resolution_failure(
    monkeypatch,
):
    study = _study()
    _add_second_same_label_concept(study)
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )
    resolution_prompt = runtime_lock["formal_resolution"]["prompt"]
    assert resolution_prompt.startswith("/no_think\n")
    assert "Claims are not independently droppable" in resolution_prompt
    assert (
        "every claim alias owned by source_ids must appear exactly once across all "
        "output nodes"
    ) in resolution_prompt
    assert sha256(resolution_prompt.encode("utf-8")).hexdigest() == (
        runtime_lock["formal_resolution"]["prompt_sha256"]
    )
    assert "formal_relation" not in runtime_lock
    assert runtime_lock["relation_verifier"]["model_id"] == (
        "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    )
    closed = []

    class Server:
        def close(self):
            closed.append(True)

    class Client:
        def __init__(self, **options):
            assert options == {"trust_env": False, "follow_redirects": False}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    calls = []

    def request_text(*_, request_document, **__):
        assert __["enable_thinking"] is False
        response_schema = __["response_format"]["json_schema"]["schema"]
        assert response_schema["properties"]["group_id"] == {
            "const": request_document["group_id"]
        }
        source_items = response_schema["properties"]["resolutions"]["items"]
        assert source_items["properties"]["source_ids"]["items"] == {
            "enum": [candidate["id"] for candidate in request_document["candidates"]]
        }
        calls.append(request_document["schema"])
        if len(calls) == 1:
            raise ConceptAPIError("CONCEPT_API_TIMEOUT")
        return json.dumps({
            "schema": "formal-concept-resolution/v1",
            "group_id": request_document["group_id"],
            "resolutions": [
                {
                    "operation": "KEEP",
                    "source_ids": [source["id"]],
                    "nodes": [{
                        "label": source["label"],
                        "claim_ids": [claim["id"] for claim in source["claims"]],
                    }],
                }
                for source in request_document["candidates"]
            ],
        })

    monkeypatch.setattr(local_generation, "start_concept_server", lambda _: Server())
    monkeypatch.setattr(local_generation.httpx, "Client", Client)
    monkeypatch.setattr(local_generation, "request_structured_text", request_text)
    knowledge_map = local_generation.generate_knowledge_map(
        study,
        {
            "runtime_lock": runtime_lock,
            "concept_api_base_url": "http://127.0.0.1:8101",
            "concept_model": runtime_lock["semantic"]["model_id"],
            "concept_max_model_len": 8_192,
        },
        "f" * 64,
        resource_context=build_map_resource_context(
            study, load_bundled_resource_library()
        ),
        resource_library=load_bundled_resource_library(),
    )

    assert calls == [
        "formal-concept-resolution-input/v1",
        "formal-concept-resolution-input/v1",
    ]
    assert closed == [True]
    assert knowledge_map["formal_concepts"]

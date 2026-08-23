from copy import deepcopy
import json
from pathlib import Path

from pdf_evidence.concept_api import ConceptAPIError

import knowledge_map.local_generation as local_generation

from knowledge_map.artifacts import (
    build_knowledge_map,
    build_knowledge_map_view,
    validate_knowledge_map,
)
from knowledge_map.formal_concepts import (
    FormalConceptError,
    build_resolution_requests,
    validate_resolution,
)
from knowledge_map.relations import (
    RelationError,
    build_relation_request,
    select_relation_pairs,
    validate_relations,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.concept_generation import claim_id, concept_id
from pdf_evidence.study_material_output import build_study_material_output
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
    second["label"] = "Second"
    second["resolution_order"] = [1, 0]
    formal = [first, second]
    pairs, status = select_relation_pairs(formal, {study["pages"][0]["page_ref"]: 1})
    assert status["processing"] == "succeeded"
    request, concept_aliases, evidence_aliases = build_relation_request(pairs[0], formal)
    pair = request["pairs"][0]
    source_evidence = next(alias for alias, owner in evidence_aliases.items() if owner[0] == concept_aliases[pair["left"]])
    target_evidence = next(alias for alias, owner in evidence_aliases.items() if owner[0] == concept_aliases[pair["right"]])
    candidate = {
        "schema": "formal-relations/v1",
        "pairs": [{
            "id": pair["id"],
            "outcome": "relations",
            "relations": [{
                "type": "prerequisite",
                "source": pair["left"],
                "target": pair["right"],
                "source_evidence_ids": [source_evidence],
                "target_evidence_ids": [target_evidence],
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
    candidate["pairs"][0]["relations"][0]["source_evidence_ids"] = [target_evidence]
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

    relation = {
        "type": "prerequisite",
        "source": pair["left"],
        "target": pair["right"],
        "source_evidence_ids": [source_evidence],
        "target_evidence_ids": [target_evidence],
    }
    for invalid_relations in (
        [relation, deepcopy(relation)],
        [
            relation,
            {
                "type": "contains",
                "source": pair["right"],
                "target": pair["left"],
                "source_evidence_ids": [target_evidence],
                "target_evidence_ids": [source_evidence],
            },
        ],
        [{**relation, "source": study["concepts"][0]["concept_id"]}],
    ):
        invalid = {
            "schema": "formal-relations/v1",
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
            "source_evidence_ids": [study["evidence_index"][0]["evidence_id"]],
            "target_evidence_ids": [study["evidence_index"][0]["evidence_id"]],
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
        material_runtime_binding_sha256="f" * 64,
    )
    assert all(relation["is_in_prerequisite_cycle"] for relation in knowledge_map["relations"])
    assert knowledge_map["initial_learning_path"] == [node["formal_concept_id"] for node in nodes]
    assert validate_knowledge_map(knowledge_map) is None
    view = build_knowledge_map_view(knowledge_map)
    assert view["schema"] == "knowledge-map-view/v3"
    assert view["relations"][0]["source_formal_concept_id"].startswith("formal-concept:")

    tampered = deepcopy(knowledge_map)
    tampered["initial_learning_path"].reverse()
    assert validate_knowledge_map(tampered) == "KNOWLEDGE_MAP_INVALID"


def test_zero_formal_concepts_stops_with_partial_reject():
    study = _study()
    knowledge_map = build_knowledge_map(
        study,
        [],
        [],
        relation_pair_status={"processing": "partial", "reason_codes": ["NO_FORMAL_CONCEPT"]},
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
        material_runtime_binding_sha256="f" * 64,
    )
    knowledge_map["formal_concepts"][0]["unexpected"] = True
    identity = dict(knowledge_map)
    identity.pop("revision")
    knowledge_map["revision"] = "knowledge-map:sha256:" + canonical_sha256(identity)
    assert validate_knowledge_map(knowledge_map) == "KNOWLEDGE_MAP_INVALID"


def test_agent3_uses_one_local_server_and_retries_only_a_temporary_failure(monkeypatch):
    study = _study()
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
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
        calls.append(request_document["schema"])
        if len(calls) == 1:
            raise ConceptAPIError("CONCEPT_API_TIMEOUT")
        source = request_document["candidates"][0]
        return json.dumps({
            "schema": "formal-concept-resolution/v1",
            "group_id": request_document["group_id"],
            "resolutions": [{
                "operation": "KEEP",
                "source_ids": [source["id"]],
                "nodes": [{
                    "label": source["label"],
                    "claim_ids": [claim["id"] for claim in source["claims"]],
                }],
            }],
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
    )

    assert calls == [
        "formal-concept-resolution-input/v1",
        "formal-concept-resolution-input/v1",
    ]
    assert closed == [True]
    assert knowledge_map["formal_concepts"]

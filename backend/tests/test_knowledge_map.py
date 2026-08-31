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
    DEDUPLICATION_OUTPUT_SCHEMA,
    FormalConceptError,
    build_deduplication_request,
    build_verifier_texts,
    canonicalize_concepts,
    uncertain_pair_decisions,
    validate_pair_decisions,
)
from knowledge_map.relations import (
    MAX_RELATION_PAIRS,
    RelationError,
    build_relation_request,
    failed_relation_artifact,
    select_relation_pairs,
    validate_relation_proposals,
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


def _verification_diagnostics(proposals, *, allowed=0, unsupported=0, failed=0):
    same = sum(pair["decision"] == "SAME" for pair in proposals)
    scored = same - unsupported - failed
    return {
        "qwen_same_pairs": same,
        "qwen_distinct_pairs": sum(
            pair["decision"] == "DISTINCT" for pair in proposals
        ),
        "qwen_uncertain_pairs": sum(
            pair["decision"] == "UNCERTAIN" for pair in proposals
        ),
        "verifier_requested_pairs": same,
        "verifier_scored_pairs": scored,
        "verifier_allowed_pairs": allowed,
        "verifier_vetoed_pairs": scored - allowed,
        "verifier_unsupported_pairs": unsupported,
        "verifier_failed_pairs": failed,
    }


def _keep_resolution(study):
    request, aliases = build_deduplication_request(study)
    decisions = uncertain_pair_decisions(request)
    return canonicalize_concepts(
        study,
        request,
        aliases,
        decisions,
        verification_diagnostics=_verification_diagnostics(decisions),
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
    artifact = _keep_resolution(study)
    repeated = _keep_resolution(study)

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
    } == {
        source["definition"]["claim_id"],
        *(point["claim_id"] for point in source["key_points"]),
    }
    assert all(claim["evidence_ids"] for claim in formal["claims"])
    assert formal["operation"] == "KEEP"
    assert formal["aliases"] == []
    assert formal["source_members"][0]["document_context_id"].startswith(
        "document-context:sha256:"
    )


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


def test_pair_decision_rejects_missing_pair_without_losing_source_coverage():
    study = _study()
    _add_second_same_label_concept(study)
    request, aliases = build_deduplication_request(study)

    with pytest.raises(FormalConceptError):
        validate_pair_decisions(
            {"schema": DEDUPLICATION_OUTPUT_SCHEMA, "pairs": []}, request
        )

    artifact = canonicalize_concepts(
        study,
        request,
        aliases,
        uncertain_pair_decisions(request),
        verification_diagnostics=_verification_diagnostics(
            uncertain_pair_decisions(request)
        ),
        failure_reason="MODEL_OUTPUT_INVALID",
    )
    assert artifact["processing"] == "partial"
    assert len(artifact["formal_concepts"]) == len(study["concepts"])
    assert artifact["diagnostics"]["coverage_before"] == 2
    assert artifact["diagnostics"]["coverage_after"] == 2


def test_invalid_model_output_keeps_every_grounded_concept():
    study = _study()
    _add_second_same_label_concept(study)
    request, aliases = build_deduplication_request(study)
    artifact = canonicalize_concepts(
        study, request, aliases, uncertain_pair_decisions(request),
        verification_diagnostics=_verification_diagnostics(
            uncertain_pair_decisions(request)
        ),
        failure_reason="MODEL_OUTPUT_INVALID",
    )
    assert artifact["processing"] == "partial"
    assert len(artifact["formal_concepts"]) == 2


def test_pair_output_allows_only_three_non_destructive_decisions():
    study = _study()
    _add_second_same_label_concept(study)
    request, _ = build_deduplication_request(study)
    pair_id = request["pairs"][0]["id"]
    for invalid in ("DROP", "SPLIT", "SUPPORT_ONLY", "MERGE"):
        with pytest.raises(FormalConceptError):
            validate_pair_decisions(
                {
                    "schema": DEDUPLICATION_OUTPUT_SCHEMA,
                    "pairs": [{"id": pair_id, "decision": invalid}],
                },
                request,
            )


def test_same_canonicalizes_array_alias_and_preserves_lineage():
    study = _study()
    first = study["concepts"][0]
    first["label"] = "Array"
    first["concept_id"] = concept_id(
        first["page_ref"], first["label"], first["definition"], first["key_points"]
    )
    second = _add_second_same_label_concept(study)
    second["label"] = "陣列"
    second["concept_id"] = concept_id(
        second["page_ref"], second["label"], second["definition"], second["key_points"]
    )
    study.pop("output_id")
    study["output_id"] = "study-material-output:sha256:" + canonical_sha256(
        study
    )
    request, aliases = build_deduplication_request(study)
    decisions = [{"id": request["pairs"][0]["id"], "decision": "SAME"}]
    artifact = canonicalize_concepts(
        study,
        request,
        aliases,
        decisions,
        verification_diagnostics=_verification_diagnostics(decisions, allowed=1),
    )
    formal = artifact["formal_concepts"][0]
    assert len(artifact["formal_concepts"]) == 1
    assert {formal["label"], *formal["aliases"]} == {"Array", "陣列"}
    assert len(formal["source_members"]) == 2
    assert {
        claim_id for member in formal["source_members"] for claim_id in member["claim_ids"]
    } == {claim["claim_id"] for claim in formal["claims"]}
    assert artifact["diagnostics"]["duplicate_delta"] == 1
    assert artifact["diagnostics"]["coverage_before"] == 2
    assert artifact["diagnostics"]["coverage_after"] == 2


def test_verifier_text_uses_semantics_without_identity_or_retrieval_metadata():
    study = _study()
    _add_second_same_label_concept(study)
    request, aliases = build_deduplication_request(study)

    texts = build_verifier_texts(study, aliases)

    assert set(texts) == set(aliases)
    encoded = "\n".join(texts.values())
    assert "Concept label:" in encoded
    assert "Claims:" in encoded
    assert "Evidence:" in encoded
    assert "Semantic headings:" in encoded
    assert all(value not in encoded for value in aliases.values())
    assert all(pair["id"] not in encoded for pair in request["pairs"])
    assert "retrieval_signals" not in encoded


@pytest.mark.parametrize(
    ("response_kind", "expected_decision", "diagnostic_field"),
    [
        ("allowed", "SAME", "verifier_allowed_pairs"),
        ("vetoed", "UNCERTAIN", "verifier_vetoed_pairs"),
        ("overflow", "UNCERTAIN", "verifier_unsupported_pairs"),
    ],
)
def test_qwen_same_requires_bidirectional_verifier(
    monkeypatch, response_kind, expected_decision, diagnostic_field
):
    study = _study()
    _add_second_same_label_concept(study)
    request, aliases = build_deduplication_request(study)
    proposals = [{"id": request["pairs"][0]["id"], "decision": "SAME"}]

    class Process:
        def request(self, verifier_request, _timeout):
            base = {
                "schema": "local-concept-equivalence-response/v1",
                "request_id": verifier_request["request_id"],
            }
            if response_kind == "overflow":
                return {
                    **base,
                    "status": "unsupported",
                    "reason_code": "VERIFIER_INPUT_TOO_LARGE",
                    "token_lengths": [385, 120],
                }
            return {
                **base,
                "status": "scored",
                "a_to_b": {
                    "entailment_probability": 0.91,
                    "argmax_label": (
                        "entailment" if response_kind == "allowed" else "neutral"
                    ),
                    "token_length": 120,
                },
                "b_to_a": {
                    "entailment_probability": 0.89,
                    "argmax_label": "entailment",
                    "token_length": 121,
                },
            }

        def close(self):
            pass

        def abort(self):
            pass

    monkeypatch.setattr(
        local_generation, "start_equivalence_process", lambda *_: Process()
    )
    decisions, diagnostics, failure = local_generation._verify_same_pairs(
        request,
        proposals,
        build_verifier_texts(study, aliases),
        {"runtime_lock": {"concept_equivalence": {"timeout_seconds": 1}}},
    )

    assert decisions == [{"id": proposals[0]["id"], "decision": expected_decision}]
    assert diagnostics[diagnostic_field] == 1
    assert failure is None


def test_verifier_failure_vetoes_every_same_proposal(monkeypatch):
    study = _study()
    _add_second_same_label_concept(study)
    request, aliases = build_deduplication_request(study)
    proposals = [{"id": request["pairs"][0]["id"], "decision": "SAME"}]

    class Process:
        def request(self, *_):
            raise LocalAIError("CHILD_TIMEOUT")

        def abort(self):
            pass

    monkeypatch.setattr(
        local_generation, "start_equivalence_process", lambda *_: Process()
    )
    decisions, diagnostics, failure = local_generation._verify_same_pairs(
        request,
        proposals,
        build_verifier_texts(study, aliases),
        {"runtime_lock": {"concept_equivalence": {"timeout_seconds": 1}}},
    )

    assert decisions == [{"id": proposals[0]["id"], "decision": "UNCERTAIN"}]
    assert diagnostics["verifier_failed_pairs"] == 1
    assert failure == "CONCEPT_EQUIVALENCE_VERIFIER_TIMEOUT"


@pytest.mark.parametrize("decision", ["DISTINCT", "UNCERTAIN"])
def test_distinct_and_uncertain_keep_tree_and_graph_traversal(decision):
    study = _study()
    first = study["concepts"][0]
    first["label"] = "Tree Traversal"
    first["concept_id"] = concept_id(
        first["page_ref"], first["label"], first["definition"], first["key_points"]
    )
    second = _add_second_same_label_concept(study)
    second["label"] = "Graph Traversal"
    second["concept_id"] = concept_id(
        second["page_ref"], second["label"], second["definition"], second["key_points"]
    )
    request, aliases = build_deduplication_request(study)
    decisions = [{"id": request["pairs"][0]["id"], "decision": decision}]
    artifact = canonicalize_concepts(
        study,
        request,
        aliases,
        decisions,
        verification_diagnostics=_verification_diagnostics(decisions),
    )
    assert len(artifact["formal_concepts"]) == 2
    assert {concept["label"] for concept in artifact["formal_concepts"]} == {
        "Tree Traversal", "Graph Traversal"
    }
    assert artifact["diagnostics"]["duplicate_delta"] == 0
    assert artifact["diagnostics"]["coverage_after"] == 2


def _relation_concept(
    number, label, claim_text, *, page_number=1, group="group:one", section="section:one"
):
    page_ref = f"page:sha256:{page_number:064x}"
    evidence_id = f"evidence:sha256:{number + 100:064x}"
    claim_id_value = f"claim:sha256:{number:064x}"
    return {
        "formal_concept_id": f"formal-concept:sha256:{number:064x}",
        "group_id": group,
        "label": label,
        "claims": [{
            "claim_id": claim_id_value,
            "text": claim_text,
            "evidence_ids": [evidence_id],
        }],
        "source_members": [{
            "source_concept_id": f"source:{number}",
            "label": label,
            "claim_ids": [claim_id_value],
            "evidence_ids": [evidence_id],
            "page_ref": page_ref,
            "document_context_id": f"document-context:sha256:{number:064x}",
            "section_ids": [section],
        }],
        "source_page_refs": [page_ref],
        "resolution_order": [number, 0],
    }


def _relation_pages(concepts):
    return {
        evidence_id: concept["source_page_refs"][0]
        for concept in concepts
        for claim in concept["claims"]
        for evidence_id in claim["evidence_ids"]
    }


def _relation_request(concepts, pairs=None):
    page_numbers = {
        concept["source_page_refs"][0]: index
        for index, concept in enumerate(concepts, start=1)
    }
    pairs = pairs or [{
        "left": concepts[0]["formal_concept_id"],
        "right": concepts[1]["formal_concept_id"],
        "signals": ["same_section"],
    }]
    return build_relation_request(pairs, concepts, page_numbers)


def _proposal(request, outcome, *, reverse=False, needs_review=False):
    pair = request["pairs"][0]
    source, target = (
        (pair["right"], pair["left"])
        if reverse else (pair["left"], pair["right"])
    )
    nodes = {node["id"]: node for node in request["nodes"]}
    selected_nodes = [nodes[source], nodes[target]]
    claim_ids = [node["claims"][0]["id"] for node in selected_nodes]
    evidence_ids = [
        node["claims"][0]["evidence_ids"][0] for node in selected_nodes
    ]
    context_ids = [node["contexts"][0]["id"] for node in selected_nodes]
    if outcome == "no_relation":
        claim_ids = evidence_ids = context_ids = []
    return {
        "schema": "formal-relation-proposals/v1",
        "pairs": [{
            "id": pair["id"],
            "outcome": outcome,
            "source": source,
            "target": target,
            "reason": "The grounded concepts have a specific learning relationship.",
            "inference_basis": {
                "kind": "combined" if outcome != "no_relation" else "claim_semantics",
                "claim_ids": claim_ids,
                "evidence_ids": evidence_ids,
                "context_ids": context_ids,
            },
            "needs_review": needs_review,
        }],
    }


def test_relation_selector_prioritizes_context_over_weak_group_and_is_bounded():
    concepts = [
        _relation_concept(
            number,
            f"Concept {number}",
            f"Concept {number} has grounded meaning.",
            page_number=number,
            group="group:shared",
            section="section:shared" if number in {1, 18} else f"section:{number}",
        )
        for number in range(1, 19)
    ]
    pages = {
        concept["source_page_refs"][0]: index
        for index, concept in enumerate(concepts, start=1)
    }

    batches, status = select_relation_pairs(concepts, pages, ceiling=1)

    assert batches[0][0]["left"] == concepts[0]["formal_concept_id"]
    assert batches[0][0]["right"] == concepts[-1]["formal_concept_id"]
    assert "same_section" in batches[0][0]["signals"]
    assert status["processing"] == "partial"
    assert status["diagnostics"]["selected_pairs"] == 1


@pytest.mark.parametrize("outcome", ["contains", "prerequisite", "related"])
def test_model_semantics_publish_without_explicit_relation_keywords(outcome):
    concepts = [
        _relation_concept(1, "Broad topic", "Broad topic explains a framework."),
        _relation_concept(2, "Focused topic", "Focused topic explains one technique."),
    ]
    request, bindings = _relation_request(concepts)
    artifact = validate_relation_proposals(
        _proposal(request, outcome),
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=_relation_pages(concepts),
        verifier=lambda *_: (False, None),
        prior_relations=[],
    )

    assert [relation["type"] for relation in artifact["relations"]] == [outcome]
    assert artifact["relations"][0]["needs_review"] is (
        outcome != "related"
    )
    assert artifact["relations"][0]["relation_context"]


def test_page_order_alone_can_return_no_relation_without_related_fallback():
    concepts = [
        _relation_concept(1, "Earlier", "Earlier explains one idea.", page_number=1),
        _relation_concept(2, "Later", "Later explains another idea.", page_number=8),
    ]
    request, bindings = _relation_request(concepts)
    artifact = validate_relation_proposals(
        _proposal(request, "no_relation"),
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=_relation_pages(concepts),
        verifier=None,
        prior_relations=[],
    )

    assert artifact["pair_outcomes"][0]["outcome"] == "no_relation"
    assert artifact["relations"] == []
    assert artifact["diagnostics"].get("model_related_pairs", 0) == 0


def test_bad_ownership_rejects_pair_but_safe_pair_remains():
    concepts = [
        _relation_concept(1, "Parent", "Parent describes a framework."),
        _relation_concept(2, "Child", "Child describes a component."),
        _relation_concept(3, "Other", "Other is unrelated."),
    ]
    pairs = [
        {"left": concepts[0]["formal_concept_id"], "right": concepts[1]["formal_concept_id"], "signals": ["same_section"]},
        {"left": concepts[1]["formal_concept_id"], "right": concepts[2]["formal_concept_id"], "signals": ["adjacent"]},
    ]
    request, bindings = _relation_request(concepts, pairs)
    first = _proposal({**request, "pairs": [request["pairs"][0]]}, "contains")["pairs"][0]
    second = _proposal({**request, "pairs": [request["pairs"][1]]}, "related")["pairs"][0]
    second["inference_basis"]["claim_ids"] = first["inference_basis"]["claim_ids"]
    artifact = validate_relation_proposals(
        {"schema": "formal-relation-proposals/v1", "pairs": [first, second]},
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=_relation_pages(concepts),
        verifier=None,
        prior_relations=[],
    )

    assert len(artifact["relations"]) == 1
    assert artifact["processing"] == "partial"
    assert artifact["rejected_pairs"][0]["reason_codes"] == [
        "RELATION_EVIDENCE_INVALID"
    ]


def test_relation_response_schema_avoids_unsupported_uniqueness_keyword():
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
    ]
    request, _ = _relation_request(concepts)
    response_schema = local_generation._relation_format(request)

    assert "uniqueItems" not in json.dumps(response_schema)
    basis = response_schema["json_schema"]["schema"]["properties"]["pairs"][
        "items"
    ]["properties"]["inference_basis"]
    assert set(basis["properties"]) == {
        "kind", "claim_ids", "evidence_ids", "context_ids"
    }


@pytest.mark.parametrize("field", ["claim_ids", "evidence_ids", "context_ids"])
def test_backend_rejects_duplicate_relation_aliases(field):
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
    ]
    request, bindings = _relation_request(concepts)
    candidate = _proposal(request, "contains")
    aliases = candidate["pairs"][0]["inference_basis"][field]
    aliases.append(aliases[0])

    artifact = validate_relation_proposals(
        candidate,
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=_relation_pages(concepts),
        verifier=None,
        prior_relations=[],
    )

    assert artifact["relations"] == []
    assert artifact["processing"] == "failed"
    assert artifact["rejected_pairs"][0]["reason_codes"] == [
        "RELATION_EVIDENCE_INVALID"
    ]


def test_reverse_conflict_and_cycles_are_rejected_deterministically():
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
    ]
    request, bindings = _relation_request(concepts)
    prior = validate_relation_proposals(
        _proposal(request, "prerequisite"),
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=_relation_pages(concepts),
        verifier=None,
        prior_relations=[],
    )["relations"]
    reverse = validate_relation_proposals(
        _proposal(request, "prerequisite", reverse=True),
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=_relation_pages(concepts),
        verifier=None,
        prior_relations=prior,
    )

    assert reverse["relations"] == []
    assert reverse["decision"] == "reject"
    assert reverse["rejected_pairs"][0]["reason_codes"] == [
        "RELATION_CONFLICT"
    ]


@pytest.mark.parametrize(
    ("relation_type", "reason_code"),
    [("prerequisite", "PREREQUISITE_CYCLE"), ("contains", "CONTAINS_CYCLE")],
)
def test_three_node_directed_cycle_is_rejected(relation_type, reason_code):
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
        _relation_concept(3, "Three", "Three is grounded."),
    ]
    evidence_pages = _relation_pages(concepts)
    prior = []
    for left, right in ((0, 1), (1, 2)):
        request, bindings = _relation_request(concepts, [{
            "left": concepts[left]["formal_concept_id"],
            "right": concepts[right]["formal_concept_id"],
            "signals": ["same_section"],
        }])
        artifact = validate_relation_proposals(
            _proposal(request, relation_type),
            request=request,
            bindings=bindings,
            formal_concepts=concepts,
            evidence_pages=evidence_pages,
            verifier=None,
            prior_relations=prior,
        )
        prior.extend(artifact["relations"])
    request, bindings = _relation_request(concepts, [{
        "left": concepts[2]["formal_concept_id"],
        "right": concepts[0]["formal_concept_id"],
        "signals": ["same_section"],
    }])
    rejected = validate_relation_proposals(
        _proposal(request, relation_type),
        request=request,
        bindings=bindings,
        formal_concepts=concepts,
        evidence_pages=evidence_pages,
        verifier=None,
        prior_relations=prior,
    )

    assert rejected["relations"] == []
    assert rejected["rejected_pairs"][0]["reason_codes"] == [reason_code]


def test_relation_proposal_permutation_replay_is_deterministic():
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
        _relation_concept(3, "Three", "Three is grounded."),
    ]
    pairs = [
        {"left": concepts[0]["formal_concept_id"], "right": concepts[1]["formal_concept_id"], "signals": ["same_section"]},
        {"left": concepts[1]["formal_concept_id"], "right": concepts[2]["formal_concept_id"], "signals": ["same_section"]},
    ]
    request, bindings = _relation_request(concepts, pairs)
    answers = [
        _proposal({**request, "pairs": [pair]}, "related")["pairs"][0]
        for pair in request["pairs"]
    ]

    def replay(items):
        return validate_relation_proposals(
            {"schema": "formal-relation-proposals/v1", "pairs": items},
            request=request,
            bindings=bindings,
            formal_concepts=concepts,
            evidence_pages=_relation_pages(concepts),
            verifier=None,
            prior_relations=[],
        )

    assert replay(answers) == replay(list(reversed(answers)))


def test_failed_relation_artifact_is_closed_and_rejects_every_pair():
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
    ]
    request, _ = _relation_request(concepts)
    artifact = failed_relation_artifact(request, "MODEL_OUTPUT_INVALID")

    assert artifact["processing"] == "failed"
    assert artifact["decision"] == "reject"
    assert len(artifact["rejected_pairs"]) == len(request["pairs"])


def test_relation_model_publishes_when_optional_verifier_is_unavailable(
    monkeypatch,
):
    concepts = [
        _relation_concept(1, "Parent", "Parent explains a broad framework."),
        _relation_concept(2, "Child", "Child explains a focused technique."),
    ]
    batch = [{
        "left": concepts[0]["formal_concept_id"],
        "right": concepts[1]["formal_concept_id"],
        "signals": ["same_section"],
    }]
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )

    class Server:
        def close(self):
            pass

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    def relation_text(*_, request_document, **__):
        return json.dumps(_proposal(request_document, "contains"))

    monkeypatch.setattr(local_generation, "start_concept_server", lambda _: Server())
    monkeypatch.setattr(local_generation.httpx, "Client", Client)
    monkeypatch.setattr(local_generation, "request_structured_text", relation_text)
    monkeypatch.setattr(
        local_generation,
        "start_relation_process",
        lambda *_: (_ for _ in ()).throw(
            LocalAIError("RELATION_VERIFIER_CUDA_UNAVAILABLE")
        ),
    )
    artifacts = local_generation._build_relation_artifacts(
        [batch],
        concepts,
        _relation_pages(concepts),
        {
            concept["source_page_refs"][0]: index
            for index, concept in enumerate(concepts, start=1)
        },
        {
            "runtime_lock": runtime_lock,
            "concept_api_base_url": "http://127.0.0.1:8101",
            "concept_model": runtime_lock["semantic"]["model_id"],
            "concept_max_model_len": 8_192,
        },
    )

    assert [relation["type"] for relation in artifacts[0]["relations"]] == [
        "contains"
    ]
    assert artifacts[0]["relations"][0]["needs_review"] is True
    assert artifacts[0]["processing"] == "partial"
    assert "RELATION_VERIFIER_CUDA_UNAVAILABLE" in artifacts[0]["reason_codes"]


def test_relation_model_invalid_output_fails_without_false_success(monkeypatch):
    concepts = [
        _relation_concept(1, "One", "One is grounded."),
        _relation_concept(2, "Two", "Two is grounded."),
    ]
    batch = [{
        "left": concepts[0]["formal_concept_id"],
        "right": concepts[1]["formal_concept_id"],
        "signals": ["adjacent"],
    }]
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )

    class Server:
        def close(self):
            pass

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(local_generation, "start_concept_server", lambda _: Server())
    monkeypatch.setattr(local_generation.httpx, "Client", Client)
    monkeypatch.setattr(
        local_generation, "request_structured_text", lambda *_, **__: "{}"
    )
    artifacts = local_generation._build_relation_artifacts(
        [batch],
        concepts,
        _relation_pages(concepts),
        {
            concept["source_page_refs"][0]: index
            for index, concept in enumerate(concepts, start=1)
        },
        {
            "runtime_lock": runtime_lock,
            "concept_api_base_url": "http://127.0.0.1:8101",
            "concept_model": runtime_lock["semantic"]["model_id"],
            "concept_max_model_len": 8_192,
        },
    )

    assert artifacts[0]["relations"] == []
    assert artifacts[0]["processing"] == "failed"
    assert artifacts[0]["reason_codes"] == ["MODEL_OUTPUT_INVALID"]


def test_map_v8_and_view_v8_bind_current_relation_contract():
    study = _study()
    first = study["concepts"][0]
    first["label"] = "Broad topic"
    first["concept_id"] = concept_id(
        first["page_ref"], first["label"], first["definition"], first["key_points"]
    )
    second = _add_second_same_label_concept(study)
    second["label"] = "Focused topic"
    second["concept_id"] = concept_id(
        second["page_ref"], second["label"], second["definition"], second["key_points"]
    )
    study.pop("output_id")
    study["output_id"] = "study-material-output:sha256:" + canonical_sha256(
        study
    )
    request, aliases = build_deduplication_request(study)
    decisions = uncertain_pair_decisions(request)
    resolution = canonicalize_concepts(
        study,
        request,
        aliases,
        decisions,
        verification_diagnostics=_verification_diagnostics(decisions),
    )
    nodes = resolution["formal_concepts"]
    page_numbers = {
        page["page_ref"]: page["page_number"] for page in study["pages"]
    }
    batches, pair_status = select_relation_pairs(nodes, page_numbers)
    relation_request, bindings = build_relation_request(
        batches[0], nodes, page_numbers
    )
    relation_artifact = validate_relation_proposals(
        _proposal(relation_request, "related"),
        request=relation_request,
        bindings=bindings,
        formal_concepts=nodes,
        evidence_pages={
            evidence["evidence_id"]: evidence["page_ref"]
            for evidence in study["evidence_index"]
        },
        verifier=None,
        prior_relations=[],
    )
    knowledge_map = build_knowledge_map(
        study,
        [resolution],
        [relation_artifact],
        relation_pair_status=pair_status,
        resource_promotion=_resource_promotion(study, nodes),
        material_runtime_binding_sha256="f" * 64,
    )
    view = build_knowledge_map_view(knowledge_map)

    assert knowledge_map["schema"] == "knowledge-map/v8"
    assert view["schema"] == "knowledge-map-view/v8"
    assert view["relations"][0]["reason"]
    assert view["relations"][0]["relation_context"]
    assert validate_knowledge_map(knowledge_map) is None


def test_map_rejects_duplicate_source_ownership():
    study = _study()
    first = _keep_resolution(study)["formal_concepts"][0]
    duplicate = deepcopy(first)
    duplicate["formal_concept_id"] = "formal-concept:sha256:" + "f" * 64
    duplicate["resolution_order"] = [1, 0]
    with pytest.raises(ValueError, match="KNOWLEDGE_MAP_CONCEPT_INVALID"):
        build_knowledge_map(
            study,
            [{"formal_concepts": [first, duplicate]}],
            [],
            relation_pair_status={
                "processing": "succeeded",
                "reason_codes": ["RELATION_REVIEW_REQUIRED"],
            },
            resource_promotion=_resource_promotion(study, [first, duplicate]),
            material_runtime_binding_sha256="f" * 64,
        )


def test_zero_formal_concepts_stops_with_partial_reject():
    study = _study()
    study["concepts"] = []
    study.pop("output_id")
    study["output_id"] = "study-material-output:sha256:" + canonical_sha256(study)
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


def test_resource_promotion_requires_exactly_one_canonical_owner():
    study = _study()
    resolution = _keep_resolution(study)
    library = _matching_resource_library(study["concepts"][0]["label"])
    context = build_map_resource_context(study, library)
    duplicate_owner = deepcopy(resolution["formal_concepts"][0])
    duplicate_owner["formal_concept_id"] = "formal-concept:sha256:" + "f" * 64

    with pytest.raises(ValueError, match="RESOURCE_PROMOTION_INPUT_INVALID"):
        promote_resources_to_formal_concepts(
            [resolution["formal_concepts"][0], duplicate_owner],
            context,
            study,
            library,
        )


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
    assert "only SAME, DISTINCT, or UNCERTAIN" in resolution_prompt
    assert "Retrieval signals are comparison hints, never merge proof" in resolution_prompt
    assert sha256(resolution_prompt.encode("utf-8")).hexdigest() == (
        runtime_lock["formal_resolution"]["prompt_sha256"]
    )
    relation_prompt = runtime_lock["formal_relation"]["prompt"]
    assert "no_relation, contains, prerequisite, or related" in relation_prompt
    assert "adjacency alone is never proof" in relation_prompt
    assert sha256(relation_prompt.encode("utf-8")).hexdigest() == (
        runtime_lock["formal_relation"]["prompt_sha256"]
    )
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

    class EquivalenceProcess:
        def request(self, verifier_request, _timeout):
            return {
                "schema": "local-concept-equivalence-response/v1",
                "request_id": verifier_request["request_id"],
                "status": "scored",
                "a_to_b": {
                    "entailment_probability": 0.91,
                    "argmax_label": "entailment",
                    "token_length": 120,
                },
                "b_to_a": {
                    "entailment_probability": 0.90,
                    "argmax_label": "entailment",
                    "token_length": 121,
                },
            }

        def close(self):
            pass

        def abort(self):
            pass

    calls = []

    def request_text(*_, request_document, **__):
        assert __["enable_thinking"] is False
        response_schema = __["response_format"]["json_schema"]["schema"]
        pair_items = response_schema["properties"]["pairs"]["items"]
        assert pair_items["properties"]["id"] == {
            "enum": [pair["id"] for pair in request_document["pairs"]]
        }
        calls.append(request_document["schema"])
        if len(calls) == 1:
            raise ConceptAPIError("CONCEPT_API_TIMEOUT")
        return json.dumps({
            "schema": "concept-deduplication/v1",
            "pairs": [
                {"id": pair["id"], "decision": "SAME"}
                for pair in request_document["pairs"]
            ],
        })

    monkeypatch.setattr(local_generation, "start_concept_server", lambda _: Server())
    monkeypatch.setattr(local_generation.httpx, "Client", Client)
    monkeypatch.setattr(local_generation, "request_structured_text", request_text)
    monkeypatch.setattr(
        local_generation,
        "start_equivalence_process",
        lambda *_: EquivalenceProcess(),
    )
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
        "concept-deduplication-input/v1",
        "concept-deduplication-input/v1",
    ]
    assert closed == [True]
    assert len(knowledge_map["formal_concepts"]) == 1
    assert knowledge_map["concept_diagnostics"]["verifier_allowed_pairs"] == 1


def test_invalid_deduplication_output_keeps_both_concepts_in_partial_map(
    monkeypatch,
):
    study = _study()
    _add_second_same_label_concept(study)
    runtime_lock = json.loads(
        (Path(__file__).parents[2] / "local_ai" / "runtime-lock.json").read_text(
            encoding="utf-8"
        )
    )

    class Server:
        def close(self):
            return None

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(local_generation, "start_concept_server", lambda _: Server())
    monkeypatch.setattr(local_generation.httpx, "Client", Client)
    monkeypatch.setattr(
        local_generation,
        "request_structured_text",
        lambda *_, **__: json.dumps({
            "schema": "concept-deduplication/v1", "pairs": []
        }),
    )
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
    assert len(knowledge_map["formal_concepts"]) == 2
    assert knowledge_map["processing"] == "partial"
    assert "MODEL_OUTPUT_INVALID" in knowledge_map["reason_codes"]

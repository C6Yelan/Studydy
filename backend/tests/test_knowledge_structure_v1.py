from copy import deepcopy
import pytest

from knowledge_map.structure import (
    SemanticState,
    _project_claim,
    apply_semantic_response,
    build_document_context,
    build_knowledge_structure,
    build_knowledge_structure_view,
    build_semantic_bundles,
    validate_knowledge_structure,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256


def _block(page: int, order: int, kind: str, text: str) -> dict:
    page_ref = "page:sha256:" + canonical_sha256(
        {"source_sha256": "1" * 64, "page_number": page}
    )
    region = [1.0, 2.0, 30.0, 40.0]
    block_id = "block:sha256:" + canonical_sha256(
        {"page_ref": page_ref, "reading_order": order, "region": region}
    )
    evidence_id = "evidence:sha256:" + canonical_sha256(
        {
            "page_ref": page_ref,
            "block_id": block_id,
            "kind": kind,
            "source": "native_text",
            "text": text,
            "reading_order": order,
            "region": region,
        }
    )
    return {
        "evidence_id": evidence_id,
        "block_id": block_id,
        "ocr_type": kind,
        "kind": kind,
        "text": text,
        "reading_order": order,
        "locator": {"page": page, "block_id": block_id, "region": region},
        "render_region": region,
        "source": "native_text",
    }


def _page(number: int, blocks: list[dict]) -> dict:
    return {
        "schema": "page-evidence/v4",
        "material_id": "material:sha256:" + "1" * 64,
        "page_ref": "page:sha256:" + canonical_sha256(
            {"source_sha256": "1" * 64, "page_number": number}
        ),
        "page_number": number,
        "evidence_blocks": blocks,
    }


def _context() -> dict:
    return build_document_context(
        [
            _page(1, [_block(1, 0, "heading", "Pointers"), _block(1, 1, "paragraph", "The null character is written as '\\0'.")]),
            _page(2, [_block(2, 0, "paragraph", "A pointer declaration can be written as int *value;")]),
            _page(3, [_block(3, 0, "heading", "Arrays"), _block(3, 1, "paragraph", "An array stores 8 values in contiguous memory.")]),
        ],
        page_count=3,
    )


def _response(context: dict) -> dict:
    evidence = context["evidence"]
    return {
        "schema": "material-semantics-response/v1",
        "concepts": [
            {
                "key": "pointer",
                "label": "Pointer",
                "aliases": ["指標"],
                "claims": [
                    {
                        "meaning": "The null character is written as \"\\0\".",
                        "source_spans": [{"evidence_id": evidence[1]["evidence_id"], "quote": "The null character is written as '\\0'."}],
                    },
                    {
                        "meaning": "int value declares a pointer",
                        "source_spans": [{"evidence_id": evidence[2]["evidence_id"], "quote": "int *value;"}],
                    },
                    {
                        "meaning": "unsupported sibling",
                        "source_spans": [{"evidence_id": evidence[2]["evidence_id"], "quote": "not in source"}],
                    },
                ],
            },
            {
                "key": "array",
                "label": "Array",
                "aliases": [],
                "claims": [{
                    "meaning": "An array stores 8 values in contiguous memory.",
                    "source_spans": [{"evidence_id": evidence[4]["evidence_id"], "quote": evidence[4]["exact_text"]}],
                }],
            },
        ],
        "relations": [
            {
                "source_concept": "pointer",
                "target_concept": "array",
                "type": "prerequisite",
                "learner_reason": "Pointer addressing is required before pointer-based array traversal.",
                "evidence_refs": [evidence[2]["evidence_id"], evidence[4]["evidence_id"]],
                "context_refs": [context["sections"][0]["section_id"], context["sections"][1]["section_id"]],
                "inference_basis": "dependency",
                "confidence": 0.82,
            }
        ],
    }


def test_material_context_bundles_sections_without_page_envelopes():
    context = _context()
    assert [section["title"] for section in context["sections"]] == ["Pointers", "Arrays"]
    assert all("previous_page" not in str(item) and "next_page" not in str(item) for item in context["evidence"])
    assert len(build_semantic_bundles(context, maximum_utf8_bytes=80_000)) == 1
    split = build_semantic_bundles(context, maximum_utf8_bytes=4096)
    assert [item["evidence_id"] for bundle in split for item in bundle["evidence"]] == [
        item["evidence_id"] for item in context["evidence"]
    ]


def test_projection_repairs_only_technical_claim_and_keeps_valid_sibling():
    context = _context()
    state = SemanticState()
    bundle = build_semantic_bundles(context, maximum_utf8_bytes=80_000)[0]
    apply_semantic_response(_response(context), context=context, bundle=bundle, state=state)
    claims = state.concepts["pointer"]["claims"]
    assert [claim["text"] for claim in claims] == [
        "The null character is written as '\\0'.",
        "int *value;",
    ]
    assert claims[0]["projection"] == "source_literal_repair"
    assert state.rejected_claims == 1
    assert state.literal_repairs == 2


@pytest.mark.parametrize(("source", "meaning"), [
    ("The terminator is '\\0'.", "The terminator is \"\\0\"."),
    ("Use the character literal 'x'.", 'Use the character literal "x".'),
    ("The condition is a <= b.", "The condition is a < b."),
    ("The answer is 42.", "The answer is 43."),
    ("The mass is 5 kg.", "The mass is 5 g."),
    ("Declare const char *value;", "Declare a character pointer."),
    ("Energy follows E = mc^2.", "Energy follows mass equivalence."),
])
def test_every_required_technical_literal_uses_source_bound_text(source, meaning):
    evidence_id = "evidence:sha256:" + "f" * 64
    projected = _project_claim(
        {"meaning": meaning, "source_spans": [{"evidence_id": evidence_id, "quote": source}]},
        {evidence_id: {"exact_text": source}},
    )
    assert projected == {
        "text": source,
        "source_spans": [{"evidence_id": evidence_id, "quote": source}],
        "projection": "source_literal_repair",
    }


def test_typed_relations_and_prerequisite_are_the_only_path_authority():
    context = _context()
    state = SemanticState()
    bundle = build_semantic_bundles(context, maximum_utf8_bytes=80_000)[0]
    response = _response(context)
    response["relations"].append({
        **deepcopy(response["relations"][0]),
        "source_concept": "array",
        "target_concept": "pointer",
        "type": "contrast",
        "learner_reason": "Compares contiguous storage with address indirection.",
        "inference_basis": "comparison",
    })
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    structure = build_knowledge_structure(
        context,
        state,
        source_sha256="1" * 64,
        run_id="run-1",
        produced_at="2026-09-05T00:00:00+00:00",
        runtime_lock_sha256=canonical_sha256({"runtime": 1}),
        model_id="Qwen/Qwen3.8-27B-FP8",
        model_revision="revision",
        semantic_calls=1,
        ocr_calls=0,
    )
    assert validate_knowledge_structure(structure)
    assert [relation["type"] for relation in structure["relations"]] == ["prerequisite", "contrast"]
    labels = {concept["concept_id"]: concept["label"] for concept in structure["concepts"]}
    assert [labels[step["concept_id"]] for step in structure["initial_learning_path"]] == ["Pointer", "Array"]
    view = build_knowledge_structure_view(structure)
    assert view["schema"] == "knowledge-structure-view/v1"
    assert view["concepts"][0]["claims"][0]["evidence"][0]["page"] == 1


def test_cycle_and_forbidden_or_generic_relations_never_publish():
    context = _context()
    state = SemanticState()
    bundle = build_semantic_bundles(context, maximum_utf8_bytes=80_000)[0]
    response = _response(context)
    reverse = deepcopy(response["relations"][0])
    reverse["source_concept"], reverse["target_concept"] = "array", "pointer"
    generic = deepcopy(response["relations"][0])
    generic["type"] = "contrast"
    generic["inference_basis"] = "comparison"
    generic["learner_reason"] = "related"
    response["relations"].extend([reverse, generic])
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    structure = build_knowledge_structure(
        context,
        state,
        source_sha256="1" * 64,
        run_id="run-1",
        produced_at="now",
        runtime_lock_sha256="a" * 64,
        model_id="Qwen/Qwen3.8-27B-FP8",
        model_revision="revision",
        semantic_calls=1,
        ocr_calls=0,
    )
    assert [relation["type"] for relation in structure["relations"]] == ["prerequisite"]
    assert structure["metrics"]["rejected_relations"] == 2


def test_cross_section_concept_has_one_primary_tree_placement_and_zero_prerequisite_path_is_complete():
    context = _context()
    state = SemanticState()
    bundle = build_semantic_bundles(context, maximum_utf8_bytes=80_000)[0]
    response = _response(context)
    response["concepts"][0]["claims"].append({
        "meaning": context["evidence"][4]["exact_text"],
        "source_spans": [{
            "evidence_id": context["evidence"][4]["evidence_id"],
            "quote": context["evidence"][4]["exact_text"],
        }],
    })
    response["relations"] = [{
        **response["relations"][0],
        "type": "application",
        "inference_basis": "usage",
        "learner_reason": "Pointer addressing is applied when traversing array storage.",
    }]
    apply_semantic_response(response, context=context, bundle=bundle, state=state)
    structure = build_knowledge_structure(
        context, state, source_sha256="1" * 64, run_id="run", produced_at="now",
        runtime_lock_sha256="a" * 64, model_id="Qwen/Qwen3.8-27B-FP8",
        model_revision="revision", semantic_calls=1, ocr_calls=0,
    )
    tree_ids = [concept_id for section in structure["document_tree"]["sections"] for concept_id in section["concept_ids"]]
    assert len(tree_ids) == len(set(tree_ids)) == len(structure["concepts"])
    assert [step["concept_id"] for step in structure["initial_learning_path"]] == [
        concept["concept_id"] for concept in structure["concepts"]
    ]


def test_later_bundle_reuses_qwen_concept_key_without_pairwise_dedup_stage():
    context = _context()
    state = SemanticState()
    sections = context["sections"]
    first_evidence = [item for item in context["evidence"] if item["section_id"] == sections[0]["section_id"]]
    second_evidence = [item for item in context["evidence"] if item["section_id"] == sections[1]["section_id"]]
    for items, section, label in (
        (first_evidence, sections[0], "Pointer"),
        (second_evidence, sections[1], "Pointer"),
    ):
        source = next(item for item in items if item["kind"] != "heading")
        apply_semantic_response(
            {
                "schema": "material-semantics-response/v1",
                "concepts": [{
                    "key": "shared-concept",
                    "label": label,
                    "aliases": [],
                    "claims": [{
                        "meaning": source["exact_text"],
                        "source_spans": [{"evidence_id": source["evidence_id"], "quote": source["exact_text"]}],
                    }],
                }],
                "relations": [],
            },
            context=context,
            bundle={"sections": [section], "evidence": items},
            state=state,
        )
    assert list(state.concepts) == ["shared-concept"]
    assert len(state.concepts["shared-concept"]["claims"]) == 2


def test_runtime_timings_do_not_change_content_revision():
    context = _context()
    state = SemanticState()
    bundle = build_semantic_bundles(context, maximum_utf8_bytes=80_000)[0]
    apply_semantic_response(_response(context), context=context, bundle=bundle, state=state)
    arguments = {
        "source_sha256": "1" * 64,
        "run_id": "run",
        "produced_at": "now",
        "runtime_lock_sha256": "a" * 64,
        "model_id": "Qwen/Qwen3.8-27B-FP8",
        "model_revision": "revision",
        "semantic_calls": 1,
        "ocr_calls": 0,
    }
    fast = build_knowledge_structure(
        context, state, evidence_duration_ms=10, semantic_duration_ms=20, **arguments
    )
    slow = build_knowledge_structure(
        context, state, evidence_duration_ms=100, semantic_duration_ms=200, **arguments
    )
    assert fast["revision"] == slow["revision"]
    assert fast["metrics"] != slow["metrics"]


def test_material_source_identity_mismatch_is_rejected_before_publication():
    context = _context()
    state = SemanticState()
    bundle = build_semantic_bundles(context, maximum_utf8_bytes=80_000)[0]
    apply_semantic_response(_response(context), context=context, bundle=bundle, state=state)
    with pytest.raises(ValueError, match="MATERIAL_IDENTITY_INVALID"):
        build_knowledge_structure(
            context, state, source_sha256="2" * 64, run_id="run", produced_at="now",
            runtime_lock_sha256="a" * 64, model_id="Qwen/Qwen3.8-27B-FP8",
            model_revision="revision", semantic_calls=1, ocr_calls=0,
        )

from copy import deepcopy

from pdf_evidence.document_context import (
    MAX_CONTEXT_TOKENS,
    build_document_contexts,
    validate_document_context,
    validate_document_context_shape,
)
from pdf_evidence.ocr_page_evidence import canonical_sha256


def _page(page_number, blocks):
    return {
        "schema": "page-evidence/v3",
        "material_id": "material:sha256:" + "a" * 64,
        "material_revision": "material-revision:sha256:" + "b" * 64,
        "section_id": f"section:sha256:{page_number:064x}",
        "page_ref": f"page:sha256:{page_number:064x}",
        "page_number": page_number,
        "page_evidence_id": f"page-evidence:sha256:{page_number:064x}",
        "evidence_blocks": [
            {
                "evidence_id": f"evidence:sha256:{page_number:032x}{index:032x}",
                "block_id": f"block:sha256:{page_number:032x}{index:032x}",
                "kind": kind,
                "text": text,
                "reading_order": index,
            }
            for index, (kind, text) in enumerate(blocks)
        ],
    }


def test_context_is_grounded_and_preserves_heading_adjacency_and_continuation():
    pages = [
        _page(1, [("heading", "Public heading"), ("paragraph", "First half:")]),
        _page(
            2,
            [
                ("paragraph", "second half."),
                ("paragraph", "A same-page neighboring explanation."),
            ],
        ),
        _page(3, [("paragraph", "Next public detail.")]),
    ]

    context = build_document_contexts(pages)[1]

    assert validate_document_context(context, pages)
    assert context["page_evidence_id"] == pages[1]["page_evidence_id"]
    assert context["current_blocks"][0]["heading_ancestry_block_ids"] == [
        pages[0]["evidence_blocks"][0]["block_id"]
    ]
    assert context["current_blocks"][0]["next_block_id"] == (
        pages[1]["evidence_blocks"][1]["block_id"]
    )
    assert context["current_blocks"][0]["continuation_block_ids"] == [
        pages[0]["evidence_blocks"][1]["block_id"]
    ]
    assert context["context_blocks"][0]["role"] == "heading_ancestry"
    assert context["context_blocks"][1]["role"] == "continuation"


def test_context_budget_drops_whole_lower_priority_blocks_not_current_evidence():
    heading = "H" * 100
    previous_continuation = "P" * 699 + ":"
    current_evidence = "C" * 2_000
    next_continuation = ")" + "N" * 500
    pages = [
        _page(1, [("heading", heading), ("paragraph", previous_continuation)]),
        _page(2, [("paragraph", current_evidence)]),
        _page(3, [("paragraph", next_continuation)]),
    ]

    context = build_document_contexts(pages)[1]

    assert context["token_count"] == 800
    assert context["token_count"] <= MAX_CONTEXT_TOKENS
    assert [block["role"] for block in context["context_blocks"]] == [
        "heading_ancestry",
        "continuation",
    ]
    assert context["current_blocks"][0]["evidence_id"] == (
        pages[1]["evidence_blocks"][0]["evidence_id"]
    )
    assert current_evidence not in str(context["context_blocks"])


def test_context_validation_rejects_changed_text_or_identity():
    pages = [
        _page(1, [("heading", "Public heading")]),
        _page(2, [("paragraph", "Public detail")]),
    ]
    context = build_document_contexts(pages)[1]

    changed_text = deepcopy(context)
    changed_text["context_blocks"][0]["text"] = "Invented heading"
    assert not validate_document_context(changed_text, pages)

    changed_page = deepcopy(context)
    changed_page["page_evidence_id"] = "page-evidence:sha256:" + "f" * 64
    assert not validate_document_context(changed_page, pages)


def test_context_owns_flat_section_identity_without_using_page_section_id():
    pages = [
        _page(1, [("heading", "Public topic"), ("paragraph", "First detail")]),
        _page(2, [("paragraph", "Continued detail")]),
    ]
    pages[0]["section_id"] = "page-section-one"
    pages[1]["section_id"] = "unrelated-page-section-two"

    contexts = build_document_contexts(pages)
    heading_section = contexts[0]["current_blocks"][0]["section_id"]

    assert contexts[0]["current_blocks"][1]["section_id"] == heading_section
    assert contexts[1]["current_blocks"][0]["section_id"] == heading_section
    assert contexts[1]["current_blocks"][0]["heading_ancestry_block_ids"] == [
        pages[0]["evidence_blocks"][0]["block_id"]
    ]
    assert all(
        raw_section not in str(context)
        for context in contexts
        for raw_section in {"page-section-one", "unrelated-page-section-two"}
    )


def test_ambiguous_heading_hierarchy_and_unheaded_section_need_review():
    pages = [
        _page(1, [("heading", "First heading"), ("paragraph", "First detail")]),
        _page(2, [("heading", "Second heading"), ("paragraph", "Second detail")]),
    ]
    second = build_document_contexts(pages)[1]

    assert second["quality"] == "needs_review"
    assert second["decision"] == "review"
    assert second["reason_codes"] == ["HEADING_HIERARCHY_AMBIGUOUS"]
    assert second["current_blocks"][1]["heading_ancestry_block_ids"] == [
        pages[1]["evidence_blocks"][0]["block_id"]
    ]

    unheaded = build_document_contexts(
        [_page(1, [("paragraph", "Public detail without heading")])]
    )[0]
    assert unheaded["reason_codes"] == ["SECTION_BOUNDARY_AMBIGUOUS"]
    assert unheaded["quality"] == "needs_review"


def test_missing_page_breaks_section_and_adjacency_instead_of_guessing():
    pages = [
        _page(1, [("heading", "Public heading"), ("paragraph", "First detail:")]),
        _page(3, [("paragraph", "Later available detail")]),
    ]

    later = build_document_contexts(pages)[1]

    assert later["reason_codes"] == ["SECTION_BOUNDARY_AMBIGUOUS"]
    assert later["current_blocks"][0]["heading_ancestry_block_ids"] == []
    assert later["current_blocks"][0]["continuation_block_ids"] == []
    assert all(
        block["role"] not in {"previous_page", "continuation"}
        for block in later["context_blocks"]
    )


def test_closed_shape_rejects_cross_material_and_section_tampering():
    pages = [
        _page(1, [("heading", "Public heading")]),
        _page(2, [("paragraph", "Public detail")]),
    ]
    context = build_document_contexts(pages)[1]

    cross_material = deepcopy(context)
    cross_material["context_blocks"][0]["material_id"] = (
        "material:sha256:" + "f" * 64
    )
    identity = dict(cross_material)
    identity.pop("context_id")
    cross_material["context_id"] = (
        "document-context:sha256:" + canonical_sha256(identity)
    )
    assert not validate_document_context_shape(cross_material)

    wrong_section = deepcopy(context)
    wrong_section_id = "document-section:sha256:" + "f" * 64
    wrong_section["current_blocks"][0]["section_id"] = wrong_section_id
    wrong_section["section_ids"] = [wrong_section_id]
    identity = dict(wrong_section)
    identity.pop("context_id")
    wrong_section["context_id"] = (
        "document-context:sha256:" + canonical_sha256(identity)
    )
    assert validate_document_context_shape(wrong_section)
    assert not validate_document_context(wrong_section, pages)

    wrong_page = deepcopy(context)
    wrong_page["context_blocks"][0]["page_ref"] = "page:sha256:" + "f" * 64
    identity = dict(wrong_page)
    identity.pop("context_id")
    wrong_page["context_id"] = (
        "document-context:sha256:" + canonical_sha256(identity)
    )
    assert validate_document_context_shape(wrong_page)
    assert not validate_document_context(wrong_page, pages)

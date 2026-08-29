from copy import deepcopy

from pdf_evidence.document_context import (
    MAX_CONTEXT_TOKENS,
    build_document_contexts,
    validate_document_context,
)


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

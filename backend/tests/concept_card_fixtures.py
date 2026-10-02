"""F4 的合成 KS，沿用正式結構建置器及既有 Evidence fixture。"""
from knowledge_map.structure import SemanticState, apply_semantic_response, build_document_context
from structure_fixtures import build_knowledge_structure
from test_knowledge_structure_v1 import _block, _page, RUN_ID, PRODUCED_AT, MODEL_REVISION

LITERAL = "if (count != 0) {\n    value = 1.0 / count; // 不可省略條件\n}\nE = m*c^2; threshold <= 0.001; '\\0'"


def card_structure():
    texts = ["A is required before B.", LITERAL, "B differs from C in ordering.", "C uses reverse ordering.", "D is independent."]
    context = build_document_context([_page(1, [
        _block(1, i, "code" if i == 1 else "paragraph", text)
        for i, text in enumerate(texts)
    ])], page_count=2, excluded_pages=[{
        "page_ref": _page(2, [])["page_ref"], "page": 2,
        "stage": "evidence", "reason_code": "NO_USABLE_EVIDENCE",
    }])
    state = SemanticState()
    state.rejected_claims = 1
    apply_semantic_response({
        "concepts": [
            {"k": key, "l": key.upper(), "a": [], "c": [{"m": None, "s": [i]} for i in refs]}
            for key, refs in [("a", [0, 1]), ("b", [2]), ("c", [3]), ("d", [4])]
        ],
        "relations": [
            {"s": "a", "t": "b", "k": "prerequisite", "r": texts[0], "e": [0], "c": 0.9},
            {"s": "b", "t": "c", "k": "contrast", "r": texts[2], "e": [2, 3], "c": 0.9},
        ],
    }, context=context, bundle={"sections": context["sections"], "evidence": context["evidence"]}, state=state)
    return build_knowledge_structure(
        context, state, source_sha256="1" * 64, run_id=RUN_ID, produced_at=PRODUCED_AT,
        runtime_lock_sha256="0" * 64, model_id="google/gemma-4-31B-it-qat-w4a16-ct",
        model_revision=MODEL_REVISION, semantic_calls=1, ocr_calls=0,
    )

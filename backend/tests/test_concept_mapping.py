"""「移到新版」的概念對應規則：以原文區塊為主、名稱為輔，不猜測拆分。"""

from knowledge_map.source_identity import map_concepts


def _document(concepts, blocks):
    """concepts: [(concept_id, label, aliases, [block 序號...])]；blocks: 原文清單。"""
    evidence = [
        {
            "evidence_id": f"e{index}-{id(blocks)}",
            "page": 1,
            "kind": "paragraph",
            "exact_text": text,
            "block_order": index,
            "source_locator": {"region": [0, index, 1, index + 1]},
        }
        for index, text in enumerate(blocks)
    ]
    return {
        "input_binding": {
            "manifest": {"items": [{"source_id": "A", "original_sha256": "1" * 64, "normalized_sha256": "2" * 64}]},
            "bundle": {"pages": [{"page": 1, "source_id": "A", "normalized_page": 1}]},
        },
        "evidence": evidence,
        "concepts": [
            {
                "concept_id": concept_id,
                "label": label,
                "aliases": aliases,
                "claims": [
                    {"claim_id": f"{concept_id}-{index}", "text": f"改寫過的重點 {concept_id} {index}",
                     "source_spans": [{"evidence_id": evidence[index]["evidence_id"], "quote": blocks[index]}]}
                    for index in indexes
                ],
            }
            for concept_id, label, aliases, indexes in concepts
        ],
    }


BLOCKS = [f"原文區塊 {index}" for index in range(10)]


def test_evidence_overlap_maps_rewritten_concepts_and_merges():
    before = _document([
        ("old-stack", "堆疊", [], [0, 1]),
        ("old-queue", "佇列", [], [2, 3]),
        ("old-deque", "雙端佇列", [], [4]),
    ], BLOCKS)
    # 新版重點文字與名稱都改寫，佇列與雙端佇列合併成一個概念。
    after = _document([
        ("new-stack", "Stack 堆疊", [], [0, 1]),
        ("new-queues", "佇列家族", [], [2, 3, 4]),
    ], list(BLOCKS))
    result = map_concepts(before, after)
    assert result["old-stack"] == {"to": "new-stack", "basis": "evidence", "reason": None, "shared": 2, "total": 2}
    assert result["old-queue"]["to"] == result["old-deque"]["to"] == "new-queues"
    assert {result[key]["basis"] for key in ("old-queue", "old-deque")} == {"evidence"}


def test_half_of_the_blocks_is_enough_but_an_even_split_is_not():
    before = _document([("old", "雜湊表", [], [0, 1, 2, 3])], BLOCKS)
    after = _document([("a", "雜湊函數", [], [0, 1]), ("b", "碰撞處理", [], [2, 3])], list(BLOCKS))
    split = map_concepts(before, after)["old"]
    assert split == {"to": None, "basis": None, "reason": "split", "shared": 2, "total": 4}
    after = _document([("a", "雜湊函數", [], [0, 1, 2]), ("b", "碰撞處理", [], [3])], list(BLOCKS))
    assert map_concepts(before, after)["old"]["to"] == "a"


def test_label_and_alias_match_only_when_unique():
    before = _document([("old", "Binary  Search", ["二分搜尋"], [5])], BLOCKS)
    # 原文區塊不同（例如教材換了頁），名稱正規化後（大小寫、空白、全半形）相同。
    after = _document([("new", "binary search", [], [6]), ("other", "Linear Search", [], [7])], list(BLOCKS))
    assert map_concepts(before, after)["old"] == {"to": "new", "basis": "label", "reason": None, "shared": 0, "total": 1}
    after = _document([("new", "ＢＩＮＡＲＹ search", [], [6]), ("other", "查找", ["二分搜尋"], [7])], list(BLOCKS))
    assert map_concepts(before, after)["old"]["reason"] == "ambiguous"
    after = _document([("new", "排序", [], [6])], list(BLOCKS))
    assert map_concepts(before, after)["old"] == {"to": None, "basis": None, "reason": "not_found", "shared": 0, "total": 1}


def test_changed_source_text_is_not_the_same_block():
    before = _document([("old", "遞迴", [], [0])], BLOCKS)
    changed = list(BLOCKS)
    changed[0] = "原文區塊 0（新版改寫）"
    after = _document([("new", "遞迴函式", [], [0])], changed)
    assert map_concepts(before, after)["old"]["reason"] == "not_found"

"""地圖區域分組：多來源依主要來源檔，否則依學習順序推導分段。"""
from runtime.storage.knowledge_reads import group_regions


def test_multiple_primary_sources_group_by_source_in_learning_order():
    path = ["c1", "c2", "c3", "c4", "c5"]
    basis, regions = group_regions(path, {
        "c1": ("s2", "02_推論.pdf"), "c2": ("s1", "01_生成.pptx"),
        "c4": ("s2", "02_推論.pdf"), "c5": ("s1", "01_生成.pptx"),
    })
    assert basis == "source"
    # 區域依第一次出現的學習順序排列；缺來源的 c3 跟隨前一個概念。
    assert [(r["label"], r["source_id"], r["concept_ids"]) for r in regions] == [
        ("02_推論", "s2", ["c1", "c4"]),
        ("01_生成", "s1", ["c2", "c3", "c5"]),
    ]


def test_leading_concept_without_source_joins_first_region():
    basis, regions = group_regions(["c0", "c1", "c2"], {
        "c1": ("s1", "a.pdf"), "c2": ("s2", "b.pdf"),
    })
    assert basis == "source"
    assert [r["concept_ids"] for r in regions] == [["c0", "c1"], ["c2"]]


def test_single_source_splits_into_at_most_four_learning_order_segments():
    for count, sizes in [(3, [3]), (8, [4, 4]), (13, [5, 4, 4]), (62, [16, 16, 15, 15])]:
        path = [f"c{i}" for i in range(count)]
        basis, regions = group_regions(path, {c: ("s1", "only.pdf") for c in path})
        assert basis == "learning_order"
        assert [len(r["concept_ids"]) for r in regions] == sizes
        assert [c for r in regions for c in r["concept_ids"]] == path
        assert all(r["source_id"] is None for r in regions)
    assert group_regions([f"c{i}" for i in range(8)], {})[1][0]["label"] == "第 1–4 個概念"
    assert group_regions(["c0"], {})[1][0]["label"] == "第 1 個概念"


def test_file_extension_is_removed_only_when_it_looks_like_one():
    _, regions = group_regions(["a", "b"], {"a": ("s1", "v1.2 講義"), "b": ("s2", "講義.docx")})
    assert [r["label"] for r in regions] == ["v1.2 講義", "講義"]

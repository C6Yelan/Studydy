"""原教材章節順序、來源歸屬與發布大綱的純函式回歸。"""
from copy import deepcopy

from runtime import note_sections


def document(sources, headings):
    sections, evidence, pages = [], [], []
    for index, (source, title) in enumerate(headings):
        page = len(pages) + 1
        pages.append({"source_id": source, "normalized_page": sum(p["source_id"] == source for p in pages) + 1})
        section_id, evidence_id = f"s{index}", f"e{index}"
        sections.append({"section_id": section_id, "title": title or "教材開頭", "order": index,
            "heading_evidence_id": evidence_id if title else None})
        evidence.append({"section_id": section_id, "evidence_id": evidence_id, "page": page,
            "block_order": 1, "kind": "heading" if title else "paragraph"})
    return {"document_tree": {"sections": sections}, "evidence": evidence,
        "input_binding": {"manifest": {"items": [{"source_id": s, "original_name": f"{s}.pdf"} for s in sources]},
            "bundle": {"pages": pages}}}


def concept(identity, *claims):
    return {"concept_id": identity, "label": identity.upper(), "claims": [
        {"claim_id": claim, "evidence": [{"evidence_id": evidence} for evidence in references]}
        for claim, references in claims]}


def test_numbered_source_hierarchy_preserves_titles_and_flattens_deep_sections():
    doc = document(["A"], [("A", title) for title in (
        "第一章 基本原理", "1.1 訓練", "1.1.1 目標", "1.1.1.1 例子", "1.2 推論", "第二章 應用", "第二節 限制")])
    values = [concept("c", ("q1", ["e2"]), ("q2", ["e4"]))]
    before = deepcopy((doc, values))
    plan = note_sections.plan(doc, values, [])
    assert [s["heading"] for s in plan] == [s["title"] for s in doc["document_tree"]["sections"]]
    assert [s["level"] for s in plan] == [1, 2, 3, 3, 2, 1, 2]
    assert [s["parent_id"] for s in plan] == [None, "s0", "s1", "s1", "s0", None, "s5"]
    assert plan[2]["claim_ids"] == ["q1"]
    assert plan[4]["claim_ids"] == ["q2"]
    assert (doc, values) == before


def test_multiple_files_follow_manifest_and_claim_uses_earliest_direct_evidence():
    doc = document(["B", "A"], [("A", "1 原理"), ("A", "1.1 細節"), ("A", "1.1.1 深層"), ("B", None)])
    values = [concept("c", ("both", ["e0", "e3"]), ("a", ["e2"]))]
    plan = note_sections.plan(doc, values, [])
    assert [s["section_id"] for s in plan] == ["source-B", "source-A", "s0", "s1", "s2"]
    assert plan[0]["claim_ids"] == ["both"]
    assert [s["level"] for s in plan] == [1, 1, 2, 3, 3]
    assert plan[4]["parent_id"] == "s0"
    assert plan[4]["claim_ids"] == ["a"]


def test_leading_body_keeps_existing_section_and_unnumbered_headings_do_not_guess_hierarchy():
    doc = document(["A"], [("A", None), ("A", "基本原理"), ("A", "訓練目標"), ("A", None)])
    values = [concept("c", ("intro", ["e0"]), ("last", ["e3"]))]
    plan = note_sections.plan(doc, values, [])
    assert [s["heading"] for s in plan] == ["教材開頭", "基本原理", "訓練目標"]
    assert [s["level"] for s in plan] == [1, 1, 1]
    assert plan[0]["claim_ids"] == ["intro"]
    assert plan[-1]["claim_ids"] == ["last"]


def test_missing_numbered_parent_does_not_attach_to_unrelated_chapter():
    doc = document(["A"], [("A", "1 原理"), ("A", "2.1 應用"), ("A", "2.1.1 例子")])
    plan = note_sections.plan(doc, [], [])
    assert [(s["level"], s["parent_id"]) for s in plan] == [(1, None), (1, None), (2, "s1")]


def test_numeric_heading_without_following_text_keeps_its_full_section_path():
    doc = document(["A"], [("A", "Chapter 1"), ("A", "1.1"), ("A", "1.1.1")])
    plan = note_sections.plan(doc, [], [])
    assert [(s["level"], s["parent_id"]) for s in plan] == [(1, None), (2, "s0"), (3, "s1")]


def test_without_source_headings_uses_map_regions_and_deduplicates_shared_claims():
    doc = document(["A"], [("A", None)])
    values = [concept("c1", ("shared", ["e0"])), concept("c2", ("shared", ["e0"]), ("q2", ["e0"]))]
    regions = [{"region_id": "r2", "label": "先學", "concept_ids": ["c2"]},
               {"region_id": "r1", "label": "再學", "concept_ids": ["c1"]}]
    plan = note_sections.plan(doc, values, regions)
    assert [s["section_id"] for s in plan] == ["r2", "r1"]
    assert [s["claim_ids"] for s in plan] == [["shared", "q2"], []]
    assert all(s["level"] == 1 and s["parent_id"] is None for s in plan)


def test_published_keeps_empty_parent_and_separates_real_explanation_from_citation():
    doc = document(["A"], [("A", "1 原理"), ("A", "1.1 基礎"), ("A", "1.1.1 延伸")])
    values = [concept("c1", ("q1", ["e1"])), concept("c2", ("q2", ["e2"]))]
    plan = note_sections.plan(doc, values, [])
    paragraphs = [{"id": "p1", "claim_ids": ["q1"], "_explained_claim_ids": ["q1"]},
                  {"id": "p2", "claim_ids": ["q2"], "_explained_claim_ids": []}]
    output = note_sections.published(plan, paragraphs, values)
    assert [s["paragraph_ids"] for s in output["sections"]] == [[], ["p1"], ["p2"]]
    assert output["sections"][0]["outline"] == [
        {"section_id": "s1", "heading": "1.1 基礎", "level": 2},
        {"section_id": "s2", "heading": "1.1.1 延伸", "level": 3}]
    assert all(not s["outline"] for s in output["sections"][1:])
    assert output["unorganized_concepts"] == [{"concept_id": "c2", "label": "C2"}]
    assert all("claim_ids" not in s for s in output["sections"])


def test_summary_keeps_written_sections_and_ancestors_in_original_order():
    doc = document(["A"], [("A", title) for title in ("1 原理", "1.1 基礎", "1.1.1 核心", "1.2 細節", "2 應用")])
    values = [concept("c1", ("q1", ["e2"])), concept("c2", ("q2", ["e3"]))]
    plan = note_sections.plan(doc, values, [])
    paragraphs = [{"id": "p1", "claim_ids": ["q1"], "_explained_claim_ids": ["q1"]}]
    before = deepcopy(plan)
    full = note_sections.published(plan, paragraphs, values, kind="full")
    summary = note_sections.published(plan, paragraphs, values, kind="summary")
    assert [s["section_id"] for s in full["sections"]] == ["s0", "s1", "s2", "s3", "s4"]
    assert [s["section_id"] for s in summary["sections"]] == ["s0", "s1", "s2", "s4"]
    assert summary["sections"][0]["outline"] == [
        {"section_id": "s1", "heading": "1.1 基礎", "level": 2},
        {"section_id": "s2", "heading": "1.1.1 核心", "level": 3}]
    assert summary["sections"][2]["paragraph_ids"] == ["p1"]
    assert summary["unorganized_concepts"] == full["unorganized_concepts"] == [{"concept_id": "c2", "label": "C2"}]
    assert plan == before


def test_empty_body_lists_all_missing_concepts_and_shared_explanation_covers_both():
    doc = document(["A"], [("A", "1 原理")])
    values = [concept("c1", ("q", ["e0"])), concept("c2", ("q", ["e0"]))]
    plan = note_sections.plan(doc, values, [])
    assert len(note_sections.published(plan, [], values)["unorganized_concepts"]) == 2
    output = note_sections.published(plan, [{"id": "p", "claim_ids": ["q"]}], values)
    assert output["unorganized_concepts"] == []
    assert output["sections"][0]["paragraph_ids"] == ["p"]

"""章節分區：驗證器、切窗、補正一次與逐窗退回；不呼叫模型或資料庫。"""
import pytest

from runtime import knowledge_chapters as kc
from runtime.storage.knowledge_reads import chapter_regions
from runtime.text_budget import estimate_prompt_tokens


def _rows(count, sources=None):
    sources = sources or ["s1"] * count
    return [
        {"concept_id": f"c{i}", "label": f"概念{i}", "pages": [i + 1], "source": sources[i]}
        for i in range(count)
    ]


def _response(*chapters):
    return {"chapters": [{"title": title, "start": start} for title, start in chapters]}


def test_chapter_range_keeps_five_to_twenty_five_concepts_per_chapter():
    assert kc.chapter_range(62) == (3, 13)
    assert kc.chapter_range(161) == (7, 33)
    assert kc.chapter_range(600) == (24, 120)
    assert kc.chapter_range(3) == (2, 2)


def test_validator_accepts_contiguous_chapters():
    parsed, errors = kc.validate(_response(("生成模型基礎", 0), ("推論與部署", 4)), 9, 2, 3)
    assert errors == []
    assert parsed == [("生成模型基礎", [0, 1, 2, 3]), ("推論與部署", [4, 5, 6, 7, 8])]


@pytest.mark.parametrize(
    "response, expected",
    [
        ({"chapters": "x"}, ["FORMAT_INVALID"]),
        ({"chapters": [{"title": "甲", "start": True}]}, ["FORMAT_INVALID"]),
        (_response(("生成模型基礎", 1), ("推論與部署", 4)), ["BOUNDARIES_INVALID"]),
        (_response(("生成模型基礎", 0), ("推論與部署", 0)), ["BOUNDARIES_INVALID"]),
        (_response(("生成模型基礎", 0), ("推論與部署", 9)), ["BOUNDARIES_INVALID"]),
        (_response(("生成模型基礎", 0)), ["COUNT_OUT_OF_RANGE:1"]),
        (_response(("生成模型基礎", 0), ("生成模型基礎", 4)), ["DUPLICATE_TITLE"]),
        (_response(("生成模型基礎", 0), ("  ", 4)), ["EMPTY_TITLE"]),
        (_response(("生成模型基礎", 0), ("其他主題", 4)), ["VAGUE_TITLE"]),
        (_response(("生成模型基礎", 0), ("第二章", 4)), ["VAGUE_TITLE"]),
        (_response(("生成模型基礎", 0), ("很" * 25, 4)), ["TITLE_TOO_LONG"]),
        (_response(("生成模型基礎", 0), ("推論與部署", 7)), ["CHAPTER_TOO_SMALL"]),
    ],
)
def test_validator_reports_each_problem(response, expected):
    assert kc.validate(response, 9, 2, 3)[1] == expected


def test_small_window_does_not_require_three_concepts_per_chapter():
    # 3 個概念時合理區數至少 2，容不下每章 3 個；不因此判定不合格。
    assert kc.validate(_response(("訊號基礎", 0), ("訊號應用", 2)), 3, 2, 2)[1] == []


def test_windows_prefer_source_changes_and_cover_every_concept_once():
    sources = ["a"] * 450 + ["b"] * 500 + ["c"] * 300
    parts = kc.windows(_rows(1250, sources))
    assert [len(p) for p in parts] == [450, 500, 300]
    assert [i for p in parts for i in p] == list(range(1250))
    # 找不到來源改變處時照上限切。
    assert [len(p) for p in kc.windows(_rows(1300))] == [600, 600, 100]
    assert kc.windows(_rows(5)) == [[0, 1, 2, 3, 4]]


def test_request_lists_sources_pages_and_suggested_count():
    rows = _rows(4, ["s1", "s1", "s2", "s2"])
    rows[1]["pages"] = [2, 3, 4, 9]
    request = kc.build_request(rows, [0, 1, 2, 3], [("s1", "01_生成.pptx"), ("s2", "v1.2 講義")])
    assert request["sources"] == [[0, "01_生成"], [1, "v1.2 講義"]]
    assert request["concepts"][1] == [1, "概念1", "2-4,9", 0]
    assert request["concepts"][2][3] == 1
    assert request["chapter_count"] == {"min": 2, "max": 2, "suggested": 2}
    assert kc.page_text([1, 3, 5, 7, 9]) == "1,3,5,7…"


def _labels(rows, label="來源檔"):
    return {row["concept_id"]: label for row in rows}


def test_compute_accepts_valid_output_within_32k_budget():
    rows = _rows(9)
    budgets = []

    def call(request, schema, budget):
        budgets.append((estimate_prompt_tokens(kc.PROMPT, request), budget))
        assert schema["properties"]["chapters"]["minItems"] == 2
        return _response(("生成模型基礎", 0), ("推論與部署", 4))

    result = kc.compute(rows, [("s1", "a.pdf")], _labels(rows), call)
    assert result["status"] == "ready" and result["calls"] == 1 and result["failed_windows"] == []
    assert [c["title"] for c in result["chapters"]] == ["生成模型基礎", "推論與部署"]
    assert result["chapters"][1]["concept_ids"] == ["c4", "c5", "c6", "c7", "c8"]
    assert all(tokens + budget == kc.CONTEXT_LIMIT for tokens, budget in budgets)


def test_compute_sends_reasons_back_once_then_accepts_the_correction():
    rows = _rows(9)
    requests = []
    answers = iter([
        _response(("其他", 0), ("推論與部署", 4)),
        _response(("生成模型基礎", 0), ("推論與部署", 4)),
    ])

    def call(request, schema, budget):
        requests.append(request)
        return next(answers)

    result = kc.compute(rows, [("s1", "a.pdf")], _labels(rows), call)
    assert result["status"] == "ready" and result["calls"] == 2
    assert "correction" not in requests[0]
    assert requests[1]["correction"]["problems"] == ["VAGUE_TITLE"]
    assert requests[1]["correction"]["previous_output"]["chapters"][0]["title"] == "其他"


def test_failed_window_falls_back_to_current_regions_without_more_retries():
    sources = ["a"] * 600 + ["b"] * 30
    rows = _rows(630, sources)
    fallback = {row["concept_id"]: ("A 檔" if row["source"] == "a" else "B 檔") for row in rows}
    calls = []

    def call(request, schema, budget):
        calls.append(len(request["concepts"]))
        if len(request["concepts"]) == 30:
            raise kc.SemanticServiceError("SEMANTIC_SERVICE_UNAVAILABLE")
        count = len(request["concepts"])
        return {"chapters": [{"title": f"主題段落{i}", "start": i * 10} for i in range(count // 10)]}

    result = kc.compute(rows, [("a", "A.pdf"), ("b", "B.pdf")], fallback, call)
    assert calls == [600, 30, 30]  # 失敗的窗只補正一次
    assert result["status"] == "fallback"
    assert result["failed_windows"] == [
        {"window": 1, "concepts": 30, "errors": ["CALL_FAILED:SEMANTIC_SERVICE_UNAVAILABLE"]}
    ]
    last = result["chapters"][-1]
    assert last == {"title": "B 檔", "concept_ids": [f"c{i}" for i in range(600, 630)], "fallback": True}
    assert len(result["chapters"]) == 61


def test_all_windows_failing_marks_the_job_failed():
    rows = _rows(9)
    result = kc.compute(rows, [("s1", "a.pdf")], _labels(rows), lambda *_: {"chapters": []})
    assert result["status"] == "failed" and result["chapters"] is None and result["calls"] == 2


def test_input_over_budget_is_not_sent():
    rows = [{**row, "label": "很長的概念名稱" * 80} for row in _rows(300)]
    result = kc.compute(rows, [("s1", "a.pdf")], _labels(rows), lambda *_: pytest.fail("sent"))
    assert result["status"] == "failed" and result["calls"] == 0
    assert result["failed_windows"][0]["errors"] == ["INPUT_TOO_LARGE"]


def test_chapter_regions_group_chapters_under_source_regions():
    path = [f"c{i}" for i in range(8)]
    sources = [
        {"region_id": "region:1", "label": "01_生成", "source_id": "s1", "concept_ids": path[:3] + path[6:]},
        {"region_id": "region:2", "label": "02_推論", "source_id": "s2", "concept_ids": path[3:6]},
    ]
    chapters = [
        {"title": "生成基礎", "concept_ids": path[:3], "fallback": False},
        {"title": "推論流程", "concept_ids": path[3:6], "fallback": False},
        {"title": "生成應用", "concept_ids": path[6:], "fallback": False},
    ]
    regions, groups = chapter_regions(path, sources, chapters)
    assert groups == [
        {"group_id": "region:1", "label": "01_生成", "source_id": "s1"},
        {"group_id": "region:2", "label": "02_推論", "source_id": "s2"},
    ]
    # 依島群排列，同群內維持學習順序。
    assert [(r["label"], r["group_id"]) for r in regions] == [
        ("生成基礎", "region:1"), ("生成應用", "region:1"), ("推論流程", "region:2"),
    ]
    # 單一來源（或沒有來源分區）時只有一層。
    regions, groups = chapter_regions(path, [], chapters)
    assert groups == [] and all(r["group_id"] is None for r in regions)
    # 與目前概念全集不一致時不採用。
    assert chapter_regions(path + ["c9"], sources, chapters) is None
    assert chapter_regions(path, sources, chapters + [chapters[0]]) is None

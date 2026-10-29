"""標記與註記錨點：真 API 與隔離 DB；重新對應、v16 區塊結構與三種匯出格式。"""
from copy import deepcopy
from io import BytesIO
from uuid import uuid4
import re
import unicodedata
import zipfile

import pymupdf
from sqlalchemy import select

from product_fixtures import ORIGIN, closed_loop
from test_note_queue import HEADERS, answer, drain, setup
from runtime import note_anchors, note_content, note_marks
from runtime.learner_session import register_account
from runtime.storage.database import database_session
from runtime.storage.tables import MaterialNotes, NoteAnnotation, NoteHighlight

TEXT = ["blocks", 0, "text"]


def ready(closed_loop, tmp_path, monkeypatch):
    f, client, path = setup(closed_loop, tmp_path, monkeypatch)
    drain(f)
    view = client.get(path + "/summary").json()
    return f, client, path, view


def post(client, path, view, start, end, style="yellow", paragraph=0, block_path=TEXT, **extra):
    text = view["paragraphs"][paragraph]["blocks"][0]["text"]
    return client.post(path + "/summary/highlights", headers=HEADERS, json={
        "expected_generation": view["generation"], "paragraph_id": str(paragraph), "block_path": block_path,
        "start": start, "end": end, "quote": text[start:end], "style": style, **extra})


def set_blocks(f, blocks):
    """把第 0 段換成指定區塊（v16 巢狀 items／table rows 結構），模擬重生後的內容。"""
    key = (f["learner"].learner_id, f["source"].material_id, f["document"]["revision"], "summary")
    with database_session(f["dsn"]) as db:
        row = db.get(MaterialNotes, key)
        paragraphs = deepcopy(row.paragraphs)
        paragraphs[0]["blocks"] = blocks
        row.paragraphs = paragraphs


def test_highlight_crud_owner_origin_and_validation(closed_loop, tmp_path, monkeypatch):
    f, client, path, view = ready(closed_loop, tmp_path, monkeypatch)
    assert client.get(path + "/summary/highlights").json() == {"highlights": []}
    created = post(client, path, view, 0, 4)
    assert created.status_code == 201, created.json()
    body = created.json()
    text = view["paragraphs"][0]["blocks"][0]["text"]
    assert (body["paragraph_id"], body["block_path"], body["start"], body["end"], body["quote"], body["style"]) == (
        "0", TEXT, 0, 4, text[:4], "yellow")
    assert body["original_paragraph"] == view["paragraphs"][0] and body["original_generation"] == 1
    assert client.get(path + "/summary/highlights").json()["highlights"] == [body]
    # Origin、錯誤範圍、樣式、路徑、過期 generation
    no_origin = client.post(path + "/summary/highlights", json={})
    assert no_origin.status_code == 403
    assert post(client, path, view, 0, 4, block_path=["blocks", 0, "items", 0, "text"]).status_code == 400
    assert client.post(path + "/summary/highlights", headers=HEADERS, json={
        "expected_generation": 1, "paragraph_id": "0", "block_path": TEXT, "start": 0, "end": 4,
        "quote": "不符", "style": "yellow"}).status_code == 400
    assert post(client, path, view, 0, 4, style="red").status_code == 400
    assert post(client, path, {**view, "generation": 9}, 0, 4).status_code == 409
    # 他人看不到也刪不掉
    foreign = register_account("highlight_foreign@example.com", "Synthetic test password 42", dsn=f["dsn"])
    client.cookies.set("studydy_session", foreign.raw_token)
    assert client.get(path + "/summary/highlights").status_code == 404
    assert client.delete(path + "/summary/highlights/" + body["highlight_id"], headers=HEADERS).status_code == 404
    client.cookies.set("studydy_session", f["token"])
    assert client.delete(path + "/summary/highlights/" + body["highlight_id"]).status_code == 403
    assert client.delete(path + "/summary/highlights/" + body["highlight_id"], headers=HEADERS).status_code == 204
    assert client.delete(path + "/summary/highlights/" + body["highlight_id"], headers=HEADERS).status_code == 404
    assert client.get(path + "/summary/highlights").json() == {"highlights": []}


def test_highlight_cannot_cover_inline_formula(closed_loop, tmp_path, monkeypatch):
    f, client, path, view = ready(closed_loop, tmp_path, monkeypatch)
    set_blocks(f, [{"type": "text", "text": "甲乙 $x^2$ 丙丁"}])
    view = client.get(path + "/summary").json()
    ok = client.post(path + "/summary/highlights", headers=HEADERS, json={
        "expected_generation": 1, "paragraph_id": "0", "block_path": TEXT, "start": 0, "end": 2, "quote": "甲乙", "style": "green"})
    assert ok.status_code == 201
    for start, end in ((3, 8), (0, 6), (4, 6)):
        assert client.post(path + "/summary/highlights", headers=HEADERS, json={
            "expected_generation": 1, "paragraph_id": "0", "block_path": TEXT, "start": start, "end": end,
            "quote": "甲乙 $x^2$ 丙丁"[start:end], "style": "green"}).status_code == 400


def test_regeneration_remaps_unique_quote_and_keeps_duplicate_or_missing_unmapped(closed_loop, tmp_path, monkeypatch):
    f, client, path, view = ready(closed_loop, tmp_path, monkeypatch)
    quotes = [view["paragraphs"][i]["blocks"][0]["text"] for i in range(3)]
    ids = [post(client, path, view, 0, len(quotes[i]), paragraph=i).json()["highlight_id"] for i in range(3)]
    client.post(path + "/summary", headers=HEADERS, json={"expected_generation": 1})

    def regenerated(request):
        value = answer(request)
        if request.get("phase") == "review_paragraph":
            return value
        paragraphs = value["paragraphs"]
        paragraphs[0]["blocks"][0]["text"] = "新增前言。" + paragraphs[0]["blocks"][0]["text"]  # 移位但仍唯一
        paragraphs[1]["blocks"][0]["text"] = "完全不同的內容"  # 失配
        paragraphs[2]["blocks"][0]["text"] += quotes[2]  # 同段 quote 出現兩次
        return value
    drain(f, regenerated)
    new = client.get(path + "/summary").json()
    assert new["generation"] == 2
    by_id = {h["highlight_id"]: h for h in client.get(path + "/summary/highlights").json()["highlights"]}
    moved, missing, duplicate = (by_id[i] for i in ids)
    assert (moved["paragraph_id"], moved["start"], moved["end"]) == ("0", 5, 5 + len(quotes[0]))
    assert new["paragraphs"][0]["blocks"][0]["text"][5:5 + len(quotes[0])] == quotes[0]
    for lost, quote in ((missing, quotes[1]), (duplicate, quotes[2])):
        assert (lost["paragraph_id"], lost["block_path"], lost["start"], lost["end"]) == (None,) * 4
        assert lost["quote"] == quote and lost["original_paragraph"]["blocks"]
    assert missing["original_generation"] == 1
    # 失配標記仍可刪除
    assert client.delete(path + "/summary/highlights/" + ids[1], headers=HEADERS).status_code == 204


def test_annotation_anchor_roundtrip_legacy_and_remap(closed_loop, tmp_path, monkeypatch):
    f, client, path, view = ready(closed_loop, tmp_path, monkeypatch)
    text = view["paragraphs"][0]["blocks"][0]["text"]
    legacy = client.put(path + f"/summary/annotations/{uuid4()}", headers=HEADERS, json={
        "expected_generation": 1, "paragraph_id": "1", "body": "舊註記"}).json()["annotations"][0]
    assert (legacy["block_path"], legacy["start"], legacy["end"], legacy["quote"]) == (None,) * 4
    assert legacy["paragraph_id"] == "1"
    anchored = str(uuid4())
    selection = {"block_path": TEXT, "start": 0, "end": len(text), "quote": text}
    saved = client.put(path + f"/summary/annotations/{anchored}", headers=HEADERS, json={
        "expected_generation": 1, "paragraph_id": "0", "body": "選取註記", **selection})
    assert saved.status_code == 200, saved.json()
    mine = next(a for a in saved.json()["annotations"] if a["annotation_id"] == anchored)
    assert {k: mine[k] for k in selection} == selection and mine["paragraph_id"] == "0"
    # 編輯只改 body，錨點不清除
    edited = client.put(path + f"/summary/annotations/{anchored}", headers=HEADERS, json={
        "expected_generation": 1, "paragraph_id": "0", "body": "改過"}).json()
    again = next(a for a in edited["annotations"] if a["annotation_id"] == anchored)
    assert again["body"] == "改過" and again["quote"] == text
    # 錨點不完整或不符
    for bad in ({"block_path": TEXT, "start": 2}, {**selection, "quote": "錯"}):
        assert client.put(path + f"/summary/annotations/{uuid4()}", headers=HEADERS, json={
            "expected_generation": 1, "paragraph_id": "0", "body": "x", **bad}).status_code in (400, 422)
    # 重生後文字改變：quote 不再存在 → 先前註記；舊註記仍依 anchor 對回
    client.post(path + "/summary", headers=HEADERS, json={"expected_generation": 1})
    def changed(request):
        value = answer(request)
        if request.get("phase") != "review_paragraph":
            value["paragraphs"][0]["blocks"][0]["text"] = "完全不同"
        return value
    drain(f, changed)
    after = {a["annotation_id"]: a for a in client.get(path + "/summary").json()["annotations"]}
    assert after[anchored]["paragraph_id"] is None and after[anchored]["quote"] == text
    assert after[anchored]["original_paragraph"]["blocks"][0]["text"] == text
    assert after[legacy["annotation_id"]]["paragraph_id"] == "1"


def test_v16_blocks_nested_items_and_table_cells_are_markable_headers_and_formulas_are_not():
    paragraph = {"id": "0", "anchor": "a", "heading": "標題", "level": 1, "blocks": [
        {"type": "text", "text": "正文"},
        {"type": "bullets", "items": [{"text": "一", "children": [{"text": "一之一", "children": [{"text": "深層"}]}]}]},
        {"type": "table", "caption": "表題", "headers": ["表頭"], "rows": [["甲", "乙"]]},
        {"type": "formula", "latex": "x"},
        {"type": "callout", "style": "reminder", "title": "標", "items": [{"text": "提醒", "children": []}]}]}
    assert note_anchors.markable(paragraph) == {
        ("blocks", 0, "text"): "正文", ("blocks", 1, "items", 0, "text"): "一",
        ("blocks", 1, "items", 0, "children", 0, "text"): "一之一",
        ("blocks", 1, "items", 0, "children", 0, "children", 0, "text"): "深層",
        ("blocks", 2, "rows", 0, 0): "甲", ("blocks", 2, "rows", 0, 1): "乙",
        ("blocks", 4, "items", 0, "text"): "提醒"}
    legacy = {"id": "0", "anchor": "a", "heading": "h", "text": "舊", "table": {"headers": ["頭"], "rows": [["格"]]}}
    assert note_anchors.markable(legacy) == {("text",): "舊", ("table", "rows", 0, 0): "格"}
    assert not note_anchors.valid_range(paragraph, ["blocks", 2, "headers", 0], 0, 2, "表頭")
    # quote 同時出現在正文與表格 → 不唯一
    item = {"original_revision": "r0", "paragraph_id": "9", "anchor": "x", "block_path": ["blocks", 0, "text"],
            "start": 0, "end": 1, "quote": "甲", "original_generation": 1}
    other = {**paragraph, "blocks": [{"type": "text", "text": "甲"}, *paragraph["blocks"][1:]]}
    assert note_anchors.locate([other], item, "r1", 1) is None
    assert note_anchors.locate([paragraph], item, "r1", 1) == ("0", ["blocks", 2, "rows", 0, 0], 0, 1)


def test_exports_apply_marks_and_list_annotated_sentence(closed_loop, tmp_path, monkeypatch):
    f, client, path, view = ready(closed_loop, tmp_path, monkeypatch)
    set_blocks(f, [{"type": "text", "text": "黃色字與粗體字、底線字、刪除字、綠藍粉。"},
                   {"type": "bullets", "items": [{"text": "條列標記", "children": []}]}])
    view = client.get(path + "/summary").json()
    text = view["paragraphs"][0]["blocks"][0]["text"]

    def mark(quote, style, base=TEXT):
        start = text.index(quote) if base == TEXT else 0
        source = text if base == TEXT else "條列標記"
        assert post(client, path, {**view, "paragraphs": [{"blocks": [{"text": source}]}]}, start, start + len(quote),
                    style=style, block_path=base).status_code == 201
    mark("黃色字", "yellow"); mark("粗體字", "bold"); mark("底線字", "underline"); mark("刪除字", "strikethrough")
    mark("綠", "green"); mark("藍", "blue"); mark("粉", "pink"); mark("條列", "yellow", ["blocks", 1, "items", 0, "text"])
    client.put(path + f"/summary/annotations/{uuid4()}", headers=HEADERS, json={
        "expected_generation": 1, "paragraph_id": "0", "body": "我的想法", "block_path": TEXT,
        "start": 0, "end": 3, "quote": "黃色字"})

    markdown = client.get(path + "/summary/export/md").text
    assert "**粗體字**" in markdown and "~~刪除字~~" in markdown and "黃色字與" in markdown
    assert "被註記原句：黃色字" in markdown and "底線字" in markdown and "<u>" not in markdown

    pdf = pymupdf.open(stream=client.get(path + "/summary/export/pdf").content, filetype="pdf")
    page_text = "".join(page.get_text() for page in pdf)
    assert "被註記原句:黃色字" in re.sub(r"\s+", "", unicodedata.normalize("NFKC", page_text))  # 字型會輸出相容漢字
    fills = {tuple(round(c * 255) for c in d["fill"]) for page in pdf for d in page.get_drawings() if d.get("fill")}
    for color in note_marks.FILL.values():
        assert tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) in fills, color

    docx = zipfile.ZipFile(BytesIO(client.get(path + "/summary/export/docx").content)).read("word/document.xml").decode()
    for fill in note_marks.FILL.values():
        assert f'w:fill="{fill}"' in docx
    for tag in ("<w:b/>", "<w:u ", "<w:strike/>"):
        assert tag in docx
    assert "被註記原句：黃色字" in docx

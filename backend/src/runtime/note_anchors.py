"""標記與註記的選取範圍：可選取的文字位置、整條公式，以及重新生成後依 quote 對回。

位置為 Unicode code point（Python str 索引），start 含、end 不含。"""
import re

# 與 frontend/src/ui/math-text.ts 的 mathParts 同一套規則：程式碼先匹配，公式以整條為單位。
# re.ASCII 使 \b 與 JavaScript 一致。
_INLINE = re.compile(
    r"```[\s\S]*?```|`[^`\n]*`|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$"
    r"|\bproduct_[A-Za-z0-9]+\b|\bsqrt\([^()\n]+\)"
    r"|\b(?:alpha|beta|gamma|theta|sigma|mu|eta|epsilon|lambda|[A-Za-z])_bar_[A-Za-z0-9]+\b"
    r"|\b[A-Za-z][A-Za-z0-9_]*\^[A-Za-z0-9]+", re.ASCII)


def formula_spans(text):
    return [m.span() for m in _INLINE.finditer(text) if not m.group(0).startswith("`")]


def _items(items, path):
    for index, item in enumerate(items):
        yield (*path, index, "text"), item["text"]
        yield from _items(item.get("children") or [], (*path, index, "children"))


def targets(paragraphs, sections=()):
    """章節獨立錨定，舊稿編號只在選取及匯出投影衍生。"""
    from .note_content import outline, numbered_heading
    numbers = {o["paragraph_id"]: o for o in outline(paragraphs)} if not sections else {}
    return [{**p, **({"display_heading": numbered_heading(numbers[p["id"]])} if p["id"] in numbers else {})}
            for p in paragraphs] + [{"target_type": "section", "id": "section:" + s["section_id"],
                "anchor": "section:" + s["section_id"], "heading": s["heading"], "level": s["level"]} for s in sections]


def markable(paragraph):
    """{路徑: 文字}：正文可見文字，包含標題、表頭、標籤與公式。"""
    from .note_content import CALLOUT_LABELS
    heading = "display_heading" if "display_heading" in paragraph else "heading"
    result = {(heading,): paragraph[heading]} if paragraph.get(heading) else {}
    if paragraph.get("target_type") == "section":
        return result
    if "blocks" not in paragraph:
        result[("text",)] = paragraph["text"]
        if paragraph.get("formula"):
            result[("formula",)] = paragraph["formula"]
        result.update({("table", "headers", c): cell for c, cell in enumerate((paragraph.get("table") or {}).get("headers", []))})
        for r, row in enumerate((paragraph.get("table") or {}).get("rows", [])):
            result.update({("table", "rows", r, c): cell for c, cell in enumerate(row)})
        return result
    for index, block in enumerate(paragraph["blocks"]):
        base, kind = ("blocks", index), block["type"]
        if kind == "text":
            result[(*base, "text")] = block["text"]
        elif kind == "formula":
            result[(*base, "latex")] = block["latex"]
        elif kind in ("bullets", "callout"):
            for path, text in _items(block["items"], (*base, "items")):
                result[path] = text
            if kind == "callout":
                result[(*base, "label")] = CALLOUT_LABELS[block["style"]]
                if block.get("title"):
                    result[(*base, "title")] = block["title"]
        elif kind == "table":
            if block.get("caption"):
                result[(*base, "caption")] = block["caption"]
            result.update({(*base, "headers", c): cell for c, cell in enumerate(block["headers"])})
            for r, row in enumerate(block["rows"]):
                result.update({(*base, "rows", r, c): cell for c, cell in enumerate(row)})
    return result


def valid_range(paragraph, path, start, end, quote):
    """範圍須等於原文 quote；公式區塊只能整條，行內公式不可部分涵蓋。"""
    text = markable(paragraph).get(tuple(path))
    return (text is not None and 0 <= start < end <= len(text) and text[start:end] == quote
            and (path[-1] not in ("latex", "formula") or (start == 0 and end == len(text)))
            and not any(start < b and end > a and not (start <= a and end >= b) for a, b in formula_spans(text)))


def locate(paragraphs, item, content_revision, content_generation):
    """回傳 (paragraph_id, 路徑, start, end)；同版（revision 與 generation 相同）原位置仍符合直接用，否則 quote 全篇唯一才對回，其餘 None。"""
    quote = item["quote"]
    same = next((p for p in paragraphs if p["id"] == item["paragraph_id"]), None)
    if ((item["original_revision"], item["original_generation"]) == (content_revision, content_generation) and same is not None and same["anchor"] == item["anchor"]
            and valid_range(same, item["block_path"], item["start"], item["end"], quote)):
        return same["id"], list(item["block_path"]), item["start"], item["end"]
    found = []
    for p in paragraphs:
        for path, text in markable(p).items():
            start = text.find(quote)
            while start != -1:
                end = start + len(quote)
                if valid_range(p, path, start, end, quote):
                    found.append((p["id"], list(path), start, end))
                    if len(found) > 1:
                        return None
                start = text.find(quote, start + 1)
    return found[0] if found else None

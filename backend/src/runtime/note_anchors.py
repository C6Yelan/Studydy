"""標記與註記的選取範圍：可選取的文字位置、行內公式排除，以及重新生成後依 quote 對回。

位置為 Unicode code point（Python str 索引），start 含、end 不含。"""
import re

# 與 frontend/src/ui/math-text.ts 的 mathParts 同一套規則：程式碼先匹配，其餘行內公式不可選取。
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


def markable(paragraph):
    """{路徑: 文字}：正文、條列與醒目區塊項目、表格資料格；不含標題、caption、表頭與公式區塊。"""
    result = {}
    if "blocks" not in paragraph:
        result[("text",)] = paragraph["text"]
        for r, row in enumerate((paragraph.get("table") or {}).get("rows", [])):
            result.update({("table", "rows", r, c): cell for c, cell in enumerate(row)})
        return result
    for index, block in enumerate(paragraph["blocks"]):
        base, kind = ("blocks", index), block["type"]
        if kind == "text":
            result[(*base, "text")] = block["text"]
        elif kind in ("bullets", "callout"):
            for path, text in _items(block["items"], (*base, "items")):
                result[path] = text
        elif kind == "table":
            for r, row in enumerate(block["rows"]):
                result.update({(*base, "rows", r, c): cell for c, cell in enumerate(row)})
    return result


def valid_range(paragraph, path, start, end, quote):
    """路徑必須是可選取文字，範圍在字串內、等於 quote，且不碰到行內公式。"""
    text = markable(paragraph).get(tuple(path))
    return (text is not None and 0 <= start < end <= len(text) and text[start:end] == quote
            and not any(start < b and end > a for a, b in formula_spans(text)))


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
            spans = formula_spans(text)
            start = text.find(quote)
            while start != -1:
                end = start + len(quote)
                if not any(start < b and end > a for a, b in spans):
                    found.append((p["id"], list(path), start, end))
                    if len(found) > 1:
                        return None
                start = text.find(quote, start + 1)
    return found[0] if found else None

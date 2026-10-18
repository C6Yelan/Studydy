"""將研究核對過的原文定位回完整 PDF；僅選段，不改原檔或捏造來源位置。"""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import math
import re
import unicodedata

import pymupdf


SCHEMA = "source-analysis-scope/v1"
_FIGURE = re.compile(
    r"\b(figure|fig\.?|table)\s*(\d+(?:\.\d+)?)|([圖表])\s*(\d+(?:\.\d+)?)", re.I
)


def _figure_refs(text):
    result = set()
    for match in _FIGURE.finditer(text):
        kind = match[1] or match[3]
        result.add(
            (
                "table" if kind.casefold() in ("table", "表") else "figure",
                match[2] or match[4],
            )
        )
    return result


def _compact(text):
    # PDF 換行與軟連字不影響定位；實際送入分析的文字仍來自原 Evidence。
    return "".join(unicodedata.normalize("NFKC", text).replace("\u00ad", "").split())


def requested_scope(data, quotes):
    values = list(
        dict.fromkeys(
            item["quote"]
            for item in quotes
            if isinstance(item.get("quote"), str) and item["quote"].strip()
        )
    )
    if not values:
        raise ValueError("SOURCE_SCOPE_EMPTY")
    return {
        "schema": SCHEMA,
        "source_sha256": sha256(data).hexdigest(),
        "quotes": values,
    }


def locate_scope(pdf, selection):
    """原文定位不到就失敗；不能以全檔分析作為不明範圍的 fallback。"""
    if selection.get("schema") != SCHEMA or not selection.get("quotes"):
        raise ValueError("SOURCE_SCOPE_INVALID")
    blocks = []
    boundaries = {}
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        for page_number, page in enumerate(document, 1):
            boundaries[page_number] = list(page.rect)
            for block in page.get_text("blocks"):
                if len(block) > 6 and block[6] != 0:
                    continue
                text = _compact(block[4])
                if text:
                    blocks.append(
                        {
                            "page": page_number,
                            "region": list(block[:4]),
                            "text": text,
                            "raw": block[4],
                        }
                    )
    position = 0
    for block in blocks:
        block["start"], block["end"] = position, position + len(block["text"])
        position = block["end"]
    fulltext = "".join(block["text"] for block in blocks)
    chosen = set()
    for quote in selection["quotes"]:
        needle = _compact(quote)
        start = fulltext.find(needle) if needle else -1
        if start < 0:
            raise ValueError("SOURCE_SCOPE_UNRESOLVED")
        # 相同短句若有多處出現，保留所有實際位置，不憑第一個命中猜頁碼。
        while start >= 0:
            end = start + len(needle)
            matched = [
                i
                for i, block in enumerate(blocks)
                if block["start"] < end and start < block["end"]
            ]
            for index in matched:
                chosen.add(index)
                # 同頁鄰接段落保留定義、否定條件與表格標題；不擴成整份文件。
                for neighbor in (index - 1, index + 1):
                    if (
                        0 <= neighbor < len(blocks)
                        and blocks[neighbor]["page"] == blocks[index]["page"]
                    ):
                        chosen.add(neighbor)
            start = fulltext.find(needle, end)
    if not chosen:
        raise ValueError("SOURCE_SCOPE_EMPTY")
    references = set().union(*(_figure_refs(quote) for quote in selection["quotes"]))
    figure_pages = set()
    if references:
        for block in blocks:
            # 圖說／表題對回同一份文件；圖表可能由多個向量／圖片區塊構成，保留該頁完整脈絡。
            first_line = block["raw"].strip().split("\n", 1)[0]
            if _FIGURE.match(first_line) and references.intersection(
                _figure_refs(first_line)
            ):
                figure_pages.add(block["page"])
    pages = defaultdict(list)
    for index in sorted(chosen):
        block = blocks[index]
        pages[block["page"]].append(block["region"])
    for page in figure_pages:
        pages[page] = [boundaries[page]]
    return {
        **deepcopy(selection),
        "pages": [
            {"page": page, "regions": regions}
            for page, regions in sorted(pages.items())
        ],
    }


def selected_pages(item):
    scope = item["analysis_scope"]
    if scope is None:
        return list(range(1, item["page_count"] + 1))
    if (
        scope.get("schema") != SCHEMA
        or scope.get("source_sha256") != item["original_sha256"]
        or not isinstance(scope.get("pages"), list)
        or not scope["pages"]
    ):
        raise ValueError("SOURCE_SCOPE_INVALID")
    numbers = []
    for page in scope["pages"]:
        number = page.get("page")
        if (
            type(number) is not int
            or not 1 <= number <= item["page_count"]
            or not page.get("regions")
        ):
            raise ValueError("SOURCE_SCOPE_INVALID")
        for box in page["regions"]:
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in box)
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                raise ValueError("SOURCE_SCOPE_INVALID")
        numbers.append(number)
    if numbers != sorted(set(numbers)):
        raise ValueError("SOURCE_SCOPE_INVALID")
    return numbers


def scoped_page(page, scope):
    if scope is None:
        return page
    boxes = next(
        item["regions"]
        for item in scope["pages"]
        if item["page"] == page["page_number"]
    )

    def included(block):
        region = block["locator"]["region"]
        return any(
            min(region[2], box[2]) > max(region[0], box[0])
            and min(region[3], box[3]) > max(region[1], box[1])
            for box in boxes
        )

    blocks = [block for block in page["evidence_blocks"] if included(block)]
    if not blocks:
        raise ValueError("SOURCE_SCOPE_UNRESOLVED")
    return {**page, "evidence_blocks": blocks}

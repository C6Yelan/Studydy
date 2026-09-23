"""從逐頁 Evidence 建立有序的教材與章節脈絡。"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .structure_rules import _CODE_OR_FORMULA, _id, _text


def _ordered_pages(pages: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(pages, list) or not pages
        or any(not isinstance(page, dict) for page in pages)
    ):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    ordered = sorted(pages, key=lambda page: page.get("page_number", 0))
    material_ids = {page.get("material_id") for page in ordered}
    page_numbers = [page.get("page_number") for page in ordered]
    if (
        len(material_ids) != 1
        or None in material_ids
        or any(type(number) is not int or number < 1 for number in page_numbers)
        or len(page_numbers) != len(set(page_numbers))
    ):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    material_id = next(iter(material_ids))
    if (
        not isinstance(material_id, str)
        or re.fullmatch(r"material:sha256:[0-9a-f]{64}", material_id) is None
    ):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    source_sha256 = material_id.removeprefix("material:sha256:")
    evidence_ids: set[str] = set()
    for page in ordered:
        blocks = page.get("evidence_blocks")
        if page.get("schema") != "page-evidence/v1" or not isinstance(blocks, list) or not blocks:
            raise ValueError("DOCUMENT_EVIDENCE_INVALID")
        if page.get("page_ref") != _id(
            "page",
            {"source_sha256": source_sha256, "page_number": page["page_number"]},
        ):
            raise ValueError("DOCUMENT_EVIDENCE_INVALID")
        orders = [block.get("reading_order") for block in blocks if isinstance(block, dict)]
        if len(orders) != len(blocks) or orders != sorted(set(orders)):
            raise ValueError("DOCUMENT_EVIDENCE_INVALID")
        for block in blocks:
            evidence_id = block.get("evidence_id")
            locator = block.get("locator")
            if (
                not isinstance(locator, dict)
                or locator.get("page") != page["page_number"]
                or not isinstance(locator.get("region"), list)
            ):
                raise ValueError("DOCUMENT_EVIDENCE_INVALID")
            block_id = _id(
                "block",
                {
                    "page_ref": page["page_ref"],
                    "reading_order": block["reading_order"],
                    "region": locator["region"],
                },
            )
            expected_evidence_id = _id(
                "evidence",
                {
                    "page_ref": page["page_ref"],
                    "block_id": block_id,
                    "kind": block.get("kind"),
                    "source": block.get("source"),
                    "text": block.get("text"),
                    "reading_order": block.get("reading_order"),
                    "region": locator["region"],
                },
            )
            if (
                block.get("block_id") != block_id
                or locator.get("block_id") != block_id
                or evidence_id != expected_evidence_id
                or evidence_id in evidence_ids
            ):
                raise ValueError("DOCUMENT_EVIDENCE_INVALID")
            evidence_ids.add(evidence_id)
    return ordered


def _non_content_evidence_ids(pages: list[dict[str, Any]]) -> set[str]:
    """只排除有邊界位置及頁面角色依據的文字；原始 Evidence 不刪除。"""

    excluded: set[str] = set()
    recurring: dict[tuple[Any, ...], list[tuple[int, str]]] = {}
    for page in pages:
        bounds = page.get("geometry", {}).get("unrotated_points")
        if not bounds:
            continue
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        for block in page["evidence_blocks"]:
            box = block["locator"]["region"]
            edge = (
                "bottom" if box[1] >= bounds[1] + height * 0.9
                else "top" if box[3] <= bounds[1] + height * 0.08 else None
            )
            text = " ".join(block["text"].split())
            if edge is None or len(text) > 200:
                continue
            # 程式中的版權字串仍是教材，不因靠近頁尾就移除。
            if re.search(r"[;{}]|(?<![<>=!])=(?!=)", text):
                continue
            if re.search(
                r"©\s*\d{4}|\bcopyright\s*(?:©|\(c\))?\s*\d{4}|all rights reserved|版權所有",
                text, re.IGNORECASE,
            ):
                excluded.add(block["evidence_id"])
                continue
            if block["kind"] == "heading" or _CODE_OR_FORMULA.search(text):
                continue
            # 頁碼須跨頁同位置且保持頁序差；固定數值不是頁碼。
            number = re.fullmatch(r"(?:(?:page|p\.)\s*)?(?:(\d+)\.)?(\d{1,4})", text, re.IGNORECASE)
            identity = (
                ("page", number[1], int(number[2]) - page["page_number"])
                if number else ("running", text)
            )
            key = (edge, round((box[0] - bounds[0]) / width, 1), *identity)
            recurring.setdefault(key, []).append((page["page_number"], block["evidence_id"]))
    for occurrences in recurring.values():
        if len({page for page, _ in occurrences}) >= 2:
            excluded.update(reference for _, reference in occurrences)
    return excluded


def build_document_context(
    pages: list[dict[str, Any]],
    *,
    page_count: int,
    excluded_pages: list[dict[str, Any]] | None = None,
    source_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """建立一次性的 material context；不產生逐頁 semantic envelope。"""

    ordered = _ordered_pages(pages)
    if type(page_count) is not int or page_count < len(ordered):
        raise ValueError("DOCUMENT_EVIDENCE_INVALID")
    material_id = ordered[0]["material_id"]
    non_content_ids = _non_content_evidence_ids(ordered)
    sections: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_included_page: int | None = None
    for page in ordered:
        if (
            source_pages and last_included_page is not None
            and source_pages[page["page_number"] - 1]["source_id"]
            != source_pages[last_included_page - 1]["source_id"]
        ):
            current = None
        if last_included_page is not None and page["page_number"] != last_included_page + 1:
            current = None
        for block in page["evidence_blocks"]:
            if current is None or block["kind"] == "heading":
                title = _text(block["text"], maximum=512) if block["kind"] == "heading" else "教材開頭"
                section_id = _id(
                    "section",
                    {
                        "material_id": material_id,
                        "anchor_evidence_id": block["evidence_id"],
                    },
                )
                current = {
                    "section_id": section_id,
                    "title": title,
                    "order": len(sections),
                    "heading_evidence_id": (
                        block["evidence_id"] if block["kind"] == "heading" else None
                    ),
                    "evidence_ids": [],
                }
                sections.append(current)
            locator = block.get("locator")
            if (
                not isinstance(locator, dict)
                or locator.get("page") != page["page_number"]
                or locator.get("block_id") != block.get("block_id")
                or not isinstance(locator.get("region"), list)
            ):
                raise ValueError("DOCUMENT_EVIDENCE_INVALID")
            item = {
                "evidence_id": block["evidence_id"],
                "page_ref": page["page_ref"],
                "page": page["page_number"],
                "block_order": block["reading_order"],
                "kind": block["kind"],
                "source": block["source"],
                "exact_text": block["text"],
                "heading": current["title"],
                "section_id": current["section_id"],
                "source_locator": {
                    "page": locator["page"],
                    "block_id": locator["block_id"],
                    "region": deepcopy(locator["region"]),
                },
            }
            evidence.append(item)
            current["evidence_ids"].append(item["evidence_id"])
        last_included_page = page["page_number"]
    if not evidence:
        raise ValueError("NO_USABLE_EVIDENCE")
    for index, section in enumerate(sections):
        section["previous_section_title"] = sections[index - 1]["title"] if index else None
        section["next_section_title"] = (
            sections[index + 1]["title"] if index + 1 < len(sections) else None
        )
    excluded = deepcopy(excluded_pages or [])
    return {
        "schema": "document-context/v1",
        **({"source_pages": deepcopy(source_pages)} if source_pages else {}),
        "material_id": material_id,
        "page_count": page_count,
        "sections": sections,
        "evidence": evidence,
        "excluded_pages": excluded,
        "non_content_evidence_ids": sorted(non_content_ids),
    }

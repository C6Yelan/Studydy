from __future__ import annotations

from typing import Any

from .ocr_page_evidence import canonical_sha256


CONTEXT_SCHEMA = "document-semantic-context/v1"
CONTEXT_POLICY = "document-reading-order-context/v1"
CONTEXT_TOKEN_COUNTER = "utf8-byte-upper-bound/v1"
MAX_CONTEXT_TOKENS = 1_024

_CONTEXT_ROLES = {
    "heading_ancestry",
    "continuation",
    "previous_page",
    "next_page",
    "supplementary",
}


def _ordered_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """確認頁面屬於同一教材，並依 1-based 頁碼固定順序。"""

    if (
        not isinstance(pages, list)
        or not pages
        or any(not isinstance(page, dict) for page in pages)
    ):
        raise ValueError("DOCUMENT_CONTEXT_INVALID")
    ordered = sorted(pages, key=lambda page: page.get("page_number", 0))
    material_ids = {page.get("material_id") for page in ordered}
    material_revisions = {page.get("material_revision") for page in ordered}
    page_numbers = [page.get("page_number") for page in ordered]
    page_refs = [page.get("page_ref") for page in ordered]
    if (
        len(material_ids) != 1
        or len(material_revisions) != 1
        or any(not isinstance(value, str) or not value for value in material_ids)
        or any(
            not isinstance(value, str) or not value
            for value in material_revisions
        )
        or any(type(number) is not int or number < 1 for number in page_numbers)
        or len(page_numbers) != len(set(page_numbers))
        or len(page_refs) != len(set(page_refs))
    ):
        raise ValueError("DOCUMENT_CONTEXT_INVALID")
    evidence_ids: set[str] = set()
    block_ids: set[str] = set()
    for page in ordered:
        blocks = page.get("evidence_blocks")
        if (
            page.get("schema") != "page-evidence/v3"
            or not isinstance(page.get("page_ref"), str)
            or not isinstance(page.get("section_id"), str)
            or not isinstance(page.get("page_evidence_id"), str)
            or not isinstance(blocks, list)
            or not blocks
        ):
            raise ValueError("DOCUMENT_CONTEXT_INVALID")
        reading_orders = [block.get("reading_order") for block in blocks]
        if (
            any(type(order) is not int or order < 0 for order in reading_orders)
            or reading_orders != sorted(reading_orders)
            or len(reading_orders) != len(set(reading_orders))
        ):
            raise ValueError("DOCUMENT_CONTEXT_INVALID")
        for block in blocks:
            if (
                not isinstance(block, dict)
                or not isinstance(block.get("evidence_id"), str)
                or not isinstance(block.get("block_id"), str)
                or not isinstance(block.get("kind"), str)
                or not isinstance(block.get("text"), str)
                or not block["text"]
                or block["evidence_id"] in evidence_ids
                or block["block_id"] in block_ids
            ):
                raise ValueError("DOCUMENT_CONTEXT_INVALID")
            evidence_ids.add(block["evidence_id"])
            block_ids.add(block["block_id"])
    return ordered


def _looks_continued(left: str, right: str) -> bool:
    """只辨識明確跨 block 未完句，不猜測教材語意。"""

    left_text = left.rstrip()
    right_text = right.lstrip()
    if not left_text or not right_text:
        return False
    left_is_open = left_text.endswith(
        (",", "，", ":", "：", ";", "；", "(", "[", "{", "=", "+", "-", "*", "/")
    )
    right_is_tail = right_text.startswith(
        (")", "]", "}", ",", "，", ";", "；", ".", "。", "+", "-", "*", "/", "=")
    )
    return left_is_open or right_is_tail


def _context_block(
    page: dict[str, Any], block: dict[str, Any], role: str
) -> dict[str, Any]:
    return {
        "role": role,
        "material_id": page["material_id"],
        "material_revision": page["material_revision"],
        "page_ref": page["page_ref"],
        "page_number": page["page_number"],
        "section_id": page["section_id"],
        "evidence_id": block["evidence_id"],
        "block_id": block["block_id"],
        "reading_order": block["reading_order"],
        "kind": block["kind"],
        "text": block["text"],
    }


def _make_context(
    ordered_pages: list[dict[str, Any]], current_index: int
) -> dict[str, Any]:
    current_page = ordered_pages[current_index]
    current_blocks = current_page["evidence_blocks"]
    previous_page = ordered_pages[current_index - 1] if current_index > 0 else None
    next_page = (
        ordered_pages[current_index + 1]
        if current_index + 1 < len(ordered_pages)
        else None
    )

    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for page in reversed(ordered_pages[:current_index]):
        for block in reversed(page["evidence_blocks"]):
            if block["kind"] == "heading":
                candidates.append(("heading_ancestry", page, block))

    if previous_page is not None and _looks_continued(
        previous_page["evidence_blocks"][-1]["text"], current_blocks[0]["text"]
    ):
        candidates.append(
            ("continuation", previous_page, previous_page["evidence_blocks"][-1])
        )
    if next_page is not None and _looks_continued(
        current_blocks[-1]["text"], next_page["evidence_blocks"][0]["text"]
    ):
        candidates.append(
            ("continuation", next_page, next_page["evidence_blocks"][0])
        )

    if previous_page is not None:
        candidates.extend(
            ("previous_page", previous_page, block)
            for block in reversed(previous_page["evidence_blocks"])
        )
    if next_page is not None:
        candidates.extend(
            ("next_page", next_page, block)
            for block in next_page["evidence_blocks"]
        )

    adjacent_refs = {
        page["page_ref"]
        for page in (previous_page, current_page, next_page)
        if page is not None
    }
    for page in ordered_pages:
        if page["page_ref"] in adjacent_refs:
            continue
        candidates.extend(
            ("supplementary", page, block)
            for block in page["evidence_blocks"]
            if block["kind"] in {"heading", "caption"}
        )

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    token_count = 0
    for role, page, block in candidates:
        if block["evidence_id"] in selected_ids:
            continue
        block_tokens = len(block["text"].encode("utf-8"))
        if token_count + block_tokens > MAX_CONTEXT_TOKENS:
            continue
        selected.append(_context_block(page, block, role))
        selected_ids.add(block["evidence_id"])
        token_count += block_tokens

    selected_block_ids = {block["block_id"] for block in selected}
    document_prefix = ordered_pages[: current_index + 1]
    current_entries = []
    for block_index, block in enumerate(current_blocks):
        preceding_headings = [
            candidate["block_id"]
            for page in document_prefix
            for candidate in page["evidence_blocks"]
            if (
                candidate["kind"] == "heading"
                and (
                    page["page_ref"] != current_page["page_ref"]
                    or candidate["reading_order"] < block["reading_order"]
                )
                and (
                    page["page_ref"] == current_page["page_ref"]
                    or candidate["block_id"] in selected_block_ids
                )
            )
        ]
        continuation_ids = []
        if block_index > 0 and _looks_continued(
            current_blocks[block_index - 1]["text"], block["text"]
        ):
            continuation_ids.append(current_blocks[block_index - 1]["block_id"])
        if block_index + 1 < len(current_blocks) and _looks_continued(
            block["text"], current_blocks[block_index + 1]["text"]
        ):
            continuation_ids.append(current_blocks[block_index + 1]["block_id"])
        if block_index == 0 and previous_page is not None:
            boundary = previous_page["evidence_blocks"][-1]
            if boundary["block_id"] in selected_block_ids and _looks_continued(
                boundary["text"], block["text"]
            ):
                continuation_ids.insert(0, boundary["block_id"])
        if block_index == len(current_blocks) - 1 and next_page is not None:
            boundary = next_page["evidence_blocks"][0]
            if boundary["block_id"] in selected_block_ids and _looks_continued(
                block["text"], boundary["text"]
            ):
                continuation_ids.append(boundary["block_id"])
        current_entries.append(
            {
                "evidence_id": block["evidence_id"],
                "block_id": block["block_id"],
                "reading_order": block["reading_order"],
                "heading_ancestry_block_ids": preceding_headings,
                "previous_block_id": (
                    current_blocks[block_index - 1]["block_id"]
                    if block_index > 0
                    else None
                ),
                "next_block_id": (
                    current_blocks[block_index + 1]["block_id"]
                    if block_index + 1 < len(current_blocks)
                    else None
                ),
                "continuation_block_ids": continuation_ids,
            }
        )

    context = {
        "schema": CONTEXT_SCHEMA,
        "material_id": current_page["material_id"],
        "material_revision": current_page["material_revision"],
        "page_ref": current_page["page_ref"],
        "page_number": current_page["page_number"],
        "section_id": current_page["section_id"],
        "page_evidence_id": current_page["page_evidence_id"],
        "current_blocks": current_entries,
        "context_blocks": selected,
        "token_budget": MAX_CONTEXT_TOKENS,
        "token_count": token_count,
        "token_counter": CONTEXT_TOKEN_COUNTER,
        "processing_policy": CONTEXT_POLICY,
    }
    context["context_id"] = (
        "document-context:sha256:" + canonical_sha256(context)
    )
    return context


def build_document_contexts(
    pages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """從已存在的 Page Evidence 建立零模型呼叫的逐頁文脈。"""

    ordered = _ordered_pages(pages)
    return [_make_context(ordered, index) for index in range(len(ordered))]


def serialize_document_context(
    context: dict[str, Any], evidence_aliases: dict[str, str]
) -> dict[str, Any]:
    """只把短 alias、關係與文字送入既有 Concept request。"""

    evidence_alias_by_id = {
        evidence_id: alias for alias, evidence_id in evidence_aliases.items()
    }
    context_alias_by_block = {
        block["block_id"]: f"c{index}"
        for index, block in enumerate(context["context_blocks"], start=1)
    }
    current_alias_by_block = {
        block["block_id"]: evidence_alias_by_id.get(block["evidence_id"])
        for block in context["current_blocks"]
    }
    block_aliases = {**context_alias_by_block, **current_alias_by_block}
    if (
        None in current_alias_by_block.values()
        or len(evidence_alias_by_id) != len(context["current_blocks"])
    ):
        raise ValueError("DOCUMENT_CONTEXT_INVALID")

    def alias(block_id: str | None) -> str | None:
        if block_id is None:
            return None
        resolved = block_aliases.get(block_id)
        if resolved is None:
            raise ValueError("DOCUMENT_CONTEXT_INVALID")
        return resolved

    return {
        "schema": "concept-context-envelope/v1",
        "document_context_id": context["context_id"],
        "current_blocks": [
            {
                "evidence_id": evidence_alias_by_id[block["evidence_id"]],
                "heading_ancestry_ids": [
                    alias(block_id)
                    for block_id in block["heading_ancestry_block_ids"]
                ],
                "previous_evidence_id": alias(block["previous_block_id"]),
                "next_evidence_id": alias(block["next_block_id"]),
                "continuation_ids": [
                    alias(block_id)
                    for block_id in block["continuation_block_ids"]
                ],
            }
            for block in context["current_blocks"]
        ],
        "context_blocks": [
            {
                "id": context_alias_by_block[block["block_id"]],
                "role": block["role"],
                "text": block["text"],
            }
            for block in context["context_blocks"]
        ],
    }


def validate_document_context(
    context: Any, pages: list[dict[str, Any]]
) -> bool:
    """以原始 Page Evidence 重建一次，避免未 grounding 的 context ref。"""

    try:
        ordered = _ordered_pages(pages)
        if not isinstance(context, dict) or set(context) != {
            "schema",
            "context_id",
            "material_id",
            "material_revision",
            "page_ref",
            "page_number",
            "section_id",
            "page_evidence_id",
            "current_blocks",
            "context_blocks",
            "token_budget",
            "token_count",
            "token_counter",
            "processing_policy",
        }:
            return False
        if context["schema"] != CONTEXT_SCHEMA:
            return False
        current_index = next(
            index
            for index, page in enumerate(ordered)
            if page["page_ref"] == context["page_ref"]
        )
        if any(
            block.get("role") not in _CONTEXT_ROLES
            for block in context["context_blocks"]
        ):
            return False
        return context == _make_context(ordered, current_index)
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        return False

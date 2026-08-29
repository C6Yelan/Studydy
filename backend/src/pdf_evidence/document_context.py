from __future__ import annotations

import re
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
_CONTEXT_REASONS = {
    "HEADING_HIERARCHY_AMBIGUOUS",
    "SECTION_BOUNDARY_AMBIGUOUS",
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


def _section_memberships(
    ordered_pages: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Heading level 不可得時，只建立最近 heading 所屬的平面 section。"""

    unheaded_start_block_id = ordered_pages[0]["evidence_blocks"][0]["block_id"]
    current_heading_id: str | None = None
    previous_page_number: int | None = None
    section_by_block: dict[str, str] = {}
    heading_by_section: dict[str, str | None] = {}
    material_revision = ordered_pages[0]["material_revision"]
    for page in ordered_pages:
        if (
            previous_page_number is not None
            and page["page_number"] != previous_page_number + 1
        ):
            current_heading_id = None
            unheaded_start_block_id = page["evidence_blocks"][0]["block_id"]
        for block in page["evidence_blocks"]:
            if block["kind"] == "heading":
                current_heading_id = block["block_id"]
            section_identity = {
                "material_revision": material_revision,
                "heading_block_id": current_heading_id,
                "unheaded_start_block_id": (
                    unheaded_start_block_id
                    if current_heading_id is None
                    else None
                ),
            }
            section_id = (
                "document-section:sha256:" + canonical_sha256(section_identity)
            )
            section_by_block[block["block_id"]] = section_id
            heading_by_section[section_id] = current_heading_id
        previous_page_number = page["page_number"]
    return section_by_block, heading_by_section


def _context_block(
    page: dict[str, Any],
    block: dict[str, Any],
    role: str,
    section_id: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "material_id": page["material_id"],
        "material_revision": page["material_revision"],
        "page_ref": page["page_ref"],
        "page_number": page["page_number"],
        "section_id": section_id,
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
    section_by_block, heading_by_section = _section_memberships(ordered_pages)
    blocks_by_id = {
        block["block_id"]: (page, block)
        for page in ordered_pages
        for block in page["evidence_blocks"]
    }
    previous_page = ordered_pages[current_index - 1] if current_index > 0 else None
    if (
        previous_page is not None
        and previous_page["page_number"] != current_page["page_number"] - 1
    ):
        previous_page = None
    next_page = (
        ordered_pages[current_index + 1]
        if current_index + 1 < len(ordered_pages)
        else None
    )
    if (
        next_page is not None
        and next_page["page_number"] != current_page["page_number"] + 1
    ):
        next_page = None

    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    current_block_ids = {block["block_id"] for block in current_blocks}
    ancestry_heading_ids = []
    for block in current_blocks:
        heading_id = heading_by_section[section_by_block[block["block_id"]]]
        if (
            heading_id is not None
            and heading_id not in current_block_ids
            and heading_id not in ancestry_heading_ids
        ):
            ancestry_heading_ids.append(heading_id)
    for heading_id in ancestry_heading_ids:
        page, block = blocks_by_id[heading_id]
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
        selected.append(
            _context_block(
                page,
                block,
                role,
                section_by_block[block["block_id"]],
            )
        )
        selected_ids.add(block["evidence_id"])
        token_count += block_tokens

    selected_block_ids = {block["block_id"] for block in selected}
    current_entries = []
    for block_index, block in enumerate(current_blocks):
        section_id = section_by_block[block["block_id"]]
        heading_id = heading_by_section[section_id]
        preceding_headings = (
            [heading_id]
            if heading_id is not None
            and heading_id != block["block_id"]
            and (
                heading_id in current_block_ids
                or heading_id in selected_block_ids
            )
            else []
        )
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
                "section_id": section_id,
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

    section_ids = list(
        dict.fromkeys(entry["section_id"] for entry in current_entries)
    )
    reasons = []
    if any(heading_by_section[section_id] is None for section_id in section_ids):
        reasons.append("SECTION_BOUNDARY_AMBIGUOUS")
    heading_count = sum(
        block["kind"] == "heading"
        for page in ordered_pages
        for block in page["evidence_blocks"]
    )
    if heading_count > 1:
        reasons.append("HEADING_HIERARCHY_AMBIGUOUS")
    reasons.sort()
    quality = "needs_review" if reasons else "accepted"
    context = {
        "schema": CONTEXT_SCHEMA,
        "material_id": current_page["material_id"],
        "material_revision": current_page["material_revision"],
        "page_ref": current_page["page_ref"],
        "page_number": current_page["page_number"],
        "section_ids": section_ids,
        "page_evidence_id": current_page["page_evidence_id"],
        "current_blocks": current_entries,
        "context_blocks": selected,
        "token_budget": MAX_CONTEXT_TOKENS,
        "token_count": token_count,
        "token_counter": CONTEXT_TOKEN_COUNTER,
        "processing_policy": CONTEXT_POLICY,
        "processing": "succeeded",
        "quality": quality,
        "decision": "review" if reasons else "retain",
        "reason_codes": reasons,
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
        if not validate_document_context_shape(context):
            return False
        current_index = next(
            index
            for index, page in enumerate(ordered)
            if page["page_ref"] == context["page_ref"]
        )
        return context == _make_context(ordered, current_index)
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        return False


def validate_document_context_shape(context: Any) -> bool:
    """供 durable consumer 重驗 closed schema、identity 與內部 references。"""

    try:
        if not isinstance(context, dict) or set(context) != {
            "schema",
            "context_id",
            "material_id",
            "material_revision",
            "page_ref",
            "page_number",
            "section_ids",
            "page_evidence_id",
            "current_blocks",
            "context_blocks",
            "token_budget",
            "token_count",
            "token_counter",
            "processing_policy",
            "processing",
            "quality",
            "decision",
            "reason_codes",
        }:
            return False
        if (
            context["schema"] != CONTEXT_SCHEMA
            or re.fullmatch(r"material:sha256:[0-9a-f]{64}", context["material_id"])
            is None
            or re.fullmatch(
                r"material-revision:sha256:[0-9a-f]{64}",
                context["material_revision"],
            )
            is None
            or re.fullmatch(r"page:sha256:[0-9a-f]{64}", context["page_ref"])
            is None
            or type(context["page_number"]) is not int
            or context["page_number"] < 1
            or not isinstance(context["section_ids"], list)
            or not context["section_ids"]
            or len(context["section_ids"]) != len(set(context["section_ids"]))
            or any(
                re.fullmatch(r"document-section:sha256:[0-9a-f]{64}", section_id)
                is None
                for section_id in context["section_ids"]
            )
            or re.fullmatch(
                r"page-evidence:sha256:[0-9a-f]{64}",
                context["page_evidence_id"],
            )
            is None
            or not isinstance(context["current_blocks"], list)
            or not context["current_blocks"]
            or not isinstance(context["context_blocks"], list)
            or context["token_budget"] != MAX_CONTEXT_TOKENS
            or context["token_counter"] != CONTEXT_TOKEN_COUNTER
            or context["processing_policy"] != CONTEXT_POLICY
            or context["processing"] != "succeeded"
            or not isinstance(context["reason_codes"], list)
            or context["reason_codes"] != sorted(set(context["reason_codes"]))
            or not set(context["reason_codes"]) <= _CONTEXT_REASONS
            or (context["quality"] == "needs_review")
            != bool(context["reason_codes"])
            or context["quality"] not in {"accepted", "needs_review"}
            or context["decision"]
            != ("review" if context["reason_codes"] else "retain")
        ):
            return False
        current_fields = {
            "evidence_id",
            "block_id",
            "reading_order",
            "section_id",
            "heading_ancestry_block_ids",
            "previous_block_id",
            "next_block_id",
            "continuation_block_ids",
        }
        context_fields = {
            "role",
            "material_id",
            "material_revision",
            "page_ref",
            "page_number",
            "section_id",
            "evidence_id",
            "block_id",
            "reading_order",
            "kind",
            "text",
        }
        if any(
            not isinstance(block, dict)
            or set(block) != current_fields
            or re.fullmatch(r"evidence:sha256:[0-9a-f]{64}", block["evidence_id"])
            is None
            or re.fullmatch(r"block:sha256:[0-9a-f]{64}", block["block_id"])
            is None
            or type(block["reading_order"]) is not int
            or block["reading_order"] < 0
            or block["section_id"] not in context["section_ids"]
            or not isinstance(block["heading_ancestry_block_ids"], list)
            or not isinstance(block["continuation_block_ids"], list)
            for block in context["current_blocks"]
        ):
            return False
        if any(
            not isinstance(block, dict)
            or set(block) != context_fields
            or block["role"] not in _CONTEXT_ROLES
            or block["material_id"] != context["material_id"]
            or block["material_revision"] != context["material_revision"]
            or re.fullmatch(r"page:sha256:[0-9a-f]{64}", block["page_ref"])
            is None
            or type(block["page_number"]) is not int
            or block["page_number"] < 1
            or re.fullmatch(
                r"document-section:sha256:[0-9a-f]{64}", block["section_id"]
            )
            is None
            or type(block["reading_order"]) is not int
            or block["reading_order"] < 0
            or re.fullmatch(r"evidence:sha256:[0-9a-f]{64}", block["evidence_id"])
            is None
            or re.fullmatch(r"block:sha256:[0-9a-f]{64}", block["block_id"])
            is None
            or not isinstance(block["kind"], str)
            or not isinstance(block["text"], str)
            or not block["text"]
            for block in context["context_blocks"]
        ):
            return False
        current_ids = {block["block_id"] for block in context["current_blocks"]}
        context_ids = {block["block_id"] for block in context["context_blocks"]}
        if (
            len(current_ids) != len(context["current_blocks"])
            or len(context_ids) != len(context["context_blocks"])
            or current_ids & context_ids
            or context["section_ids"]
            != list(
                dict.fromkeys(
                    block["section_id"] for block in context["current_blocks"]
                )
            )
        ):
            return False
        known_ids = current_ids | context_ids
        references = [
            reference
            for block in context["current_blocks"]
            for reference in [
                *block["heading_ancestry_block_ids"],
                block["previous_block_id"],
                block["next_block_id"],
                *block["continuation_block_ids"],
            ]
            if reference is not None
        ]
        if any(reference not in known_ids for reference in references):
            return False
        token_count = sum(
            len(block["text"].encode("utf-8"))
            for block in context["context_blocks"]
        )
        identity = dict(context)
        context_id = identity.pop("context_id")
        if any(
            block.get("role") not in _CONTEXT_ROLES
            for block in context["context_blocks"]
        ):
            return False
        return (
            context["token_count"] == token_count
            and token_count <= MAX_CONTEXT_TOKENS
            and context_id
            == "document-context:sha256:" + canonical_sha256(identity)
        )
    except (AttributeError, KeyError, StopIteration, TypeError, ValueError):
        return False

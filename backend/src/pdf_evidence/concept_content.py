from __future__ import annotations

from copy import deepcopy
from typing import Any

from .concept_deduplication import CONCEPT_GROUP_SCHEMA


SUMMARY_CONTEXT_SCHEMA = "s1-concept-summary-context/v1"
EVIDENCE_SUMMARY_SCHEMA = "s1-evidence-summary/v1"
CONCEPT_KEYWORDS_SCHEMA = "s1-concept-keywords/v1"
MAX_CONTENT_GROUPS = 8


def _collect_group_content(
    groups: Any,
) -> tuple[str, list[dict[str, Any]], bool] | None:
    """讀取內容產生所需的群組欄位，並保留來源 review 狀態。"""
    if not isinstance(groups, list) or not groups:
        return None

    material_ref = None
    content_groups = []
    source_needs_review = False
    for group in groups:
        if group["schema"] != CONCEPT_GROUP_SCHEMA:
            return None
        status = (
            group["processing"],
            group["quality"],
            group["decision"],
            group["reason_code"],
        )
        if status == (
            "succeeded",
            "accepted",
            "retain",
            "CONCEPT_GROUP_ACCEPTED",
        ):
            pass
        elif status == (
            "succeeded",
            "needs_review",
            "review",
            "CONCEPT_GROUP_SEMANTIC_CONFLICT",
        ):
            source_needs_review = True
        else:
            return None

        group_material_ref = group["material_ref"]
        group_id = group["group_id"]
        normalized_name = group["normalized_name"]
        members = group["members"]
        if material_ref is None:
            material_ref = group_material_ref
        elif group_material_ref != material_ref:
            return None

        content_members = []
        for member in members:
            source_page = member["source_page"]
            evidence = member["evidence"]
            if source_page["material_ref"] != material_ref:
                return None
            content_members.append(
                {
                    "candidate_id": member["candidate_id"],
                    "page_ref": source_page["page_ref"],
                    "page_number": source_page["page_number"],
                    "name": member["name"],
                    "definition": member["definition"],
                    "scope": member["scope"],
                    "evidence_ids": [
                        reference["evidence_id"] for reference in evidence
                    ],
                }
            )
        content_members.sort(key=lambda member: member["candidate_id"])
        content_groups.append(
            {
                "group_id": group_id,
                "normalized_name": normalized_name,
                "members": content_members,
            }
        )

    content_groups.sort(key=lambda group: group["group_id"])
    return material_ref, content_groups, source_needs_review


def build_summary_context(groups: Any) -> dict[str, Any] | None:
    """建立同一教材、最多八個群組的精簡摘要 context。"""
    collected = _collect_group_content(groups)
    if collected is None or len(groups) > MAX_CONTENT_GROUPS:
        return None
    material_ref, content_groups, _ = collected
    return {
        "schema": SUMMARY_CONTEXT_SCHEMA,
        "material_ref": material_ref,
        "groups": content_groups,
    }


def build_evidence_summary(context: Any, body: Any) -> dict[str, Any]:
    """驗證摘要 body 並將內容綁回 context 中的已知 Evidence。"""
    failure = {
        "schema": EVIDENCE_SUMMARY_SCHEMA,
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
    }
    if not isinstance(body, dict) or set(body) != {"summary", "evidence_ids"}:
        return {**failure, "reason_code": "EVIDENCE_SUMMARY_BODY_INVALID"}
    if not isinstance(body["summary"], str) or not body["summary"].strip():
        return {**failure, "reason_code": "EVIDENCE_SUMMARY_BODY_INVALID"}
    evidence_ids = body["evidence_ids"]
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(
            not isinstance(evidence_id, str) or not evidence_id.strip()
            for evidence_id in evidence_ids
        )
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        return {**failure, "reason_code": "EVIDENCE_SUMMARY_BODY_INVALID"}

    known_evidence_ids = {
        evidence_id
        for group in context["groups"]
        for member in group["members"]
        for evidence_id in member["evidence_ids"]
    }
    if any(evidence_id not in known_evidence_ids for evidence_id in evidence_ids):
        return {**failure, "reason_code": "EVIDENCE_SUMMARY_EVIDENCE_INVALID"}
    return {
        "schema": EVIDENCE_SUMMARY_SCHEMA,
        "development_only": True,
        "material_ref": context["material_ref"],
        "source_group_ids": [group["group_id"] for group in context["groups"]],
        "summary": body["summary"],
        "evidence_ids": deepcopy(evidence_ids),
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_code": "EVIDENCE_SUMMARY_NEEDS_REVIEW",
    }


def build_concept_keywords(groups: Any) -> dict[str, Any]:
    """依群組名稱建立最多八個有 Evidence 依據且排序固定的關鍵字。"""
    failure = {
        "schema": CONCEPT_KEYWORDS_SCHEMA,
        "keywords": [],
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
    }
    if isinstance(groups, list) and not groups:
        return {**failure, "reason_code": "CONCEPT_KEYWORDS_EVIDENCE_MISSING"}
    collected = _collect_group_content(groups)
    if collected is None:
        return {**failure, "reason_code": "CONCEPT_KEYWORDS_INPUT_INVALID"}
    material_ref, content_groups, source_needs_review = collected

    ordered_groups = sorted(
        content_groups,
        key=lambda group: (group["normalized_name"], group["group_id"]),
    )
    limited = len(ordered_groups) > MAX_CONTENT_GROUPS
    keywords = []
    for group in ordered_groups[:MAX_CONTENT_GROUPS]:
        evidence_ids = sorted(
            {
                evidence_id
                for member in group["members"]
                for evidence_id in member["evidence_ids"]
            }
        )
        if not evidence_ids:
            return {
                **failure,
                "reason_code": "CONCEPT_KEYWORDS_EVIDENCE_MISSING",
            }
        keywords.append(
            {
                "keyword": group["normalized_name"],
                "group_id": group["group_id"],
                "evidence_ids": evidence_ids,
            }
        )

    needs_review = limited or source_needs_review
    if limited:
        reason_code = "CONCEPT_KEYWORDS_LIMIT_APPLIED"
    elif source_needs_review:
        reason_code = "CONCEPT_KEYWORDS_SOURCE_NEEDS_REVIEW"
    else:
        reason_code = "CONCEPT_KEYWORDS_ACCEPTED"
    return {
        "schema": CONCEPT_KEYWORDS_SCHEMA,
        "material_ref": material_ref,
        "keywords": keywords,
        "processing": "succeeded",
        "quality": "needs_review" if needs_review else "accepted",
        "decision": "review" if needs_review else "retain",
        "reason_code": reason_code,
    }

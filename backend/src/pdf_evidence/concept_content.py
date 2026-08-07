from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from .concept_deduplication import CONCEPT_GROUP_SCHEMA


SUMMARY_CONTEXT_SCHEMA = "concept-summary-context/v1"
EVIDENCE_SUMMARY_SCHEMA = "evidence-summary/v1"
CONCEPT_KEYWORDS_SCHEMA = "concept-keywords/v1"
CONCEPT_CONTENT_SCHEMA = "concept-content/v2"
CONCEPT_CONTENT_PROMPT_VERSION = "concept-content-prompt/v3"
RELATION_CLUE_KINDS = (
    "prerequisite",
    "contains",
    "example",
    "contrast",
    "application",
    "sequence",
    "diagram_connection",
)
RELATION_DIRECTIONS = ("source_to_target", "bidirectional", "none")
CONCEPT_CONTENT_PROMPT = (
    "Use only the supplied same-material concept groups. Return one JSON object with exactly "
    "summary, summary_evidence_ids, and relation_clues. The summary must state only claims found "
    "in the source material. Do not use the summary to describe the input, concept-group count, "
    "Evidence IDs, prompt, model, processing state, source availability or sufficiency, or missing "
    "context. Keep summary within 1000 characters. "
    "Return at most 8 relation clues; each clue must use an allowed kind and direction_hint, "
    "reference two different supplied group IDs, keep statement within 300 characters, and cite "
    "known Evidence IDs from both groups. Clues are reviewable observations, not formal Relations "
    "or a graph. Do not invent facts, groups, Evidence, a Knowledge Map, or a Learning Path."
)
CONCEPT_CONTENT_PROMPT_SHA256 = hashlib.sha256(
    CONCEPT_CONTENT_PROMPT.encode("utf-8")
).hexdigest()
CONCEPT_CONTENT_BODY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "summary_evidence_ids", "relation_clues"],
    "properties": {
        "summary": {
            "type": "string",
            "pattern": r".*\S.*",
            "maxLength": 1000,
        },
        "summary_evidence_ids": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": r".*\S.*"},
        },
        "relation_clues": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "source_group_id",
                    "target_group_id",
                    "statement",
                    "direction_hint",
                    "evidence_ids",
                ],
                "properties": {
                    "kind": {"type": "string", "enum": list(RELATION_CLUE_KINDS)},
                    "source_group_id": {"type": "string", "pattern": r".*\S.*"},
                    "target_group_id": {"type": "string", "pattern": r".*\S.*"},
                    "statement": {
                        "type": "string",
                        "pattern": r".*\S.*",
                        "maxLength": 300,
                    },
                    "direction_hint": {
                        "type": "string",
                        "enum": list(RELATION_DIRECTIONS),
                    },
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": r".*\S.*"},
                    },
                },
            },
        },
    },
}
MAX_CONTENT_GROUPS = 8
MAX_SUMMARY_CHARACTERS = 1000
MAX_RELATION_CLUES = 8
MAX_RELATION_STATEMENT_CHARACTERS = 300


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


def _concept_content_failure(reason_code: str) -> dict[str, Any]:
    """建立不夾帶未驗證內容的固定失敗結果。"""
    return {
        "schema": CONCEPT_CONTENT_SCHEMA,
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }


def _has_valid_evidence_ids(evidence_ids: Any) -> bool:
    """確認 Evidence ID 是非空、不重複的字串清單。"""
    return (
        isinstance(evidence_ids, list)
        and bool(evidence_ids)
        and all(
            isinstance(evidence_id, str) and bool(evidence_id.strip())
            for evidence_id in evidence_ids
        )
        and len(evidence_ids) == len(set(evidence_ids))
    )


def build_concept_content(context: Any, body: Any) -> dict[str, Any]:
    """驗證摘要與關聯線索，並綁回群組雙方的已知 Evidence。"""
    body_fields = {"summary", "summary_evidence_ids", "relation_clues"}
    if not isinstance(body, dict) or set(body) != body_fields:
        return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")

    summary = body["summary"]
    summary_evidence_ids = body["summary_evidence_ids"]
    relation_clues = body["relation_clues"]
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > MAX_SUMMARY_CHARACTERS
    ):
        return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")
    if not _has_valid_evidence_ids(summary_evidence_ids):
        return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")
    if (
        not isinstance(relation_clues, list)
        or len(relation_clues) > MAX_RELATION_CLUES
    ):
        return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")

    group_evidence_ids = {
        group["group_id"]: {
            evidence_id
            for member in group["members"]
            for evidence_id in member["evidence_ids"]
        }
        for group in context["groups"]
    }
    known_evidence_ids = set().union(*group_evidence_ids.values())
    if any(
        evidence_id not in known_evidence_ids
        for evidence_id in summary_evidence_ids
    ):
        return _concept_content_failure("CONCEPT_CONTENT_EVIDENCE_INVALID")

    clue_fields = {
        "kind",
        "source_group_id",
        "target_group_id",
        "statement",
        "direction_hint",
        "evidence_ids",
    }
    clue_keys = set()
    checked_clues = []
    for clue in relation_clues:
        if not isinstance(clue, dict) or set(clue) != clue_fields:
            return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")

        kind = clue["kind"]
        source_group_id = clue["source_group_id"]
        target_group_id = clue["target_group_id"]
        statement = clue["statement"]
        direction_hint = clue["direction_hint"]
        if not all(
            isinstance(value, str)
            for value in (
                kind,
                source_group_id,
                target_group_id,
                statement,
                direction_hint,
            )
        ):
            return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")

        kind = kind.strip()
        source_group_id = source_group_id.strip()
        target_group_id = target_group_id.strip()
        statement = statement.strip()
        direction_hint = direction_hint.strip()
        if (
            kind not in RELATION_CLUE_KINDS
            or direction_hint not in RELATION_DIRECTIONS
            or not statement
            or len(statement) > MAX_RELATION_STATEMENT_CHARACTERS
        ):
            return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")
        if (
            source_group_id not in group_evidence_ids
            or target_group_id not in group_evidence_ids
            or source_group_id == target_group_id
        ):
            return _concept_content_failure("CONCEPT_CONTENT_GROUP_INVALID")

        evidence_ids = clue["evidence_ids"]
        if not _has_valid_evidence_ids(evidence_ids):
            return _concept_content_failure("CONCEPT_CONTENT_BODY_INVALID")
        if any(
            evidence_id not in known_evidence_ids for evidence_id in evidence_ids
        ):
            return _concept_content_failure("CONCEPT_CONTENT_EVIDENCE_INVALID")
        clue_evidence_ids = set(evidence_ids)
        if (
            not clue_evidence_ids.intersection(
                group_evidence_ids[source_group_id]
            )
            or not clue_evidence_ids.intersection(
                group_evidence_ids[target_group_id]
            )
        ):
            return _concept_content_failure("CONCEPT_CONTENT_EVIDENCE_INVALID")

        clue_key = (
            kind,
            source_group_id,
            target_group_id,
            statement,
            direction_hint,
        )
        if clue_key in clue_keys:
            return _concept_content_failure("CONCEPT_CONTENT_CLUE_DUPLICATE")
        clue_keys.add(clue_key)
        checked_clues.append(
            {
                "kind": kind,
                "source_group_id": source_group_id,
                "target_group_id": target_group_id,
                "statement": statement,
                "direction_hint": direction_hint,
                "evidence_ids": deepcopy(evidence_ids),
            }
        )

    reason_code = (
        "CONCEPT_CONTENT_NEEDS_REVIEW"
        if checked_clues
        else "CONCEPT_CONTENT_NO_RELATION_CLUES"
    )
    return {
        "schema": CONCEPT_CONTENT_SCHEMA,
        "development_only": True,
        "material_ref": context["material_ref"],
        "source_group_ids": [group["group_id"] for group in context["groups"]],
        "summary": summary,
        "summary_evidence_ids": deepcopy(summary_evidence_ids),
        "relation_clues": checked_clues,
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_code": reason_code,
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

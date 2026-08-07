from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unicodedata
from typing import Any


CONCEPT_GROUP_SCHEMA = "concept-group/v1"


def normalize_concept_name(name: Any) -> str | None:
    """以固定 Unicode 與空白規則正規化 Concept 名稱。"""
    if not isinstance(name, str):
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", name).casefold().split())
    return normalized or None


def group_concept_candidates(candidates: Any) -> list[dict[str, Any]] | None:
    """將同教材且名稱完全相同的 accepted candidates 組成穩定群組。"""
    if not isinstance(candidates, list) or not candidates:
        return None

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    candidate_ids: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return None
        identity = candidate.get("identity")
        evidence = candidate.get("evidence")
        candidate_id = candidate.get("candidate_id")
        if (
            candidate.get("schema") != "concept-candidate/v1"
            or candidate.get("processing") != "succeeded"
            or candidate.get("quality") != "accepted"
            or candidate.get("decision") != "retain"
            or candidate.get("reason_code") != "CONCEPT_CANDIDATE_ACCEPTED"
            or not isinstance(candidate_id, str)
            or not candidate_id.strip()
            or candidate_id in candidate_ids
            or not isinstance(identity, dict)
            or not isinstance(identity.get("material_ref"), str)
            or not identity["material_ref"].strip()
            or not isinstance(identity.get("page_ref"), str)
            or not identity["page_ref"].strip()
            or isinstance(identity.get("page_number"), bool)
            or not isinstance(identity.get("page_number"), int)
            or identity["page_number"] < 1
            or any(
                not isinstance(candidate.get(field), str)
                or not candidate[field].strip()
                for field in ("name", "definition", "scope")
            )
            or not isinstance(evidence, list)
            or not evidence
            or any(
                not isinstance(reference, dict)
                or not isinstance(reference.get("evidence_id"), str)
                or not reference["evidence_id"].strip()
                for reference in evidence
            )
        ):
            return None

        normalized_name = normalize_concept_name(candidate["name"])
        if normalized_name is None:
            return None
        candidate_ids.add(candidate_id)
        member = {
            "candidate_id": candidate_id,
            "source_page": {
                "material_ref": identity["material_ref"],
                "page_ref": identity["page_ref"],
                "page_number": identity["page_number"],
            },
            "name": candidate["name"],
            "definition": candidate["definition"],
            "scope": candidate["scope"],
            "evidence": deepcopy(evidence),
        }
        grouped.setdefault(
            (identity["material_ref"], normalized_name), []
        ).append(member)

    groups = []
    for (material_ref, normalized_name), members in grouped.items():
        members.sort(key=lambda member: member["candidate_id"])
        group_identity = {
            "schema": CONCEPT_GROUP_SCHEMA,
            "material_ref": material_ref,
            "normalized_name": normalized_name,
            "member_candidate_ids": [
                member["candidate_id"] for member in members
            ],
        }
        encoded_identity = json.dumps(
            group_identity,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        group_id = (
            "concept-group:sha256:"
            f"{hashlib.sha256(encoded_identity).hexdigest()}"
        )
        has_conflict = (
            len({member["definition"] for member in members}) > 1
            or len({member["scope"] for member in members}) > 1
        )
        groups.append(
            {
                "schema": CONCEPT_GROUP_SCHEMA,
                "group_id": group_id,
                "material_ref": material_ref,
                "normalized_name": normalized_name,
                "members": members,
                "processing": "succeeded",
                "quality": "needs_review" if has_conflict else "accepted",
                "decision": "review" if has_conflict else "retain",
                "reason_code": (
                    "CONCEPT_GROUP_SEMANTIC_CONFLICT"
                    if has_conflict
                    else "CONCEPT_GROUP_ACCEPTED"
                ),
            }
        )

    groups.sort(key=lambda group: group["group_id"])
    return groups

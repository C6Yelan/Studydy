from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from handoff.contract import is_handoff_consumer_eligible_package
from material_runtime_files import canonical_json_bytes


__all__ = ("build_concept_rule_decisions",)

_SCHEMA_VERSION = "concept-rule-decisions/v1"
_STRONG_EVIDENCE_KINDS = frozenset(
    {"definition", "explicit_alias", "heading"}
)
_DEFINITION_EVIDENCE_KIND = "definition"
_EDGE_PUNCTUATION = (
    " \t\r\n:：;；,.，。!?！？"
    "\"'“”‘’()（）[]【】<>《》"
)
_STRUCTURAL_NOISE_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十百千0-9]+章|"
    r"(?:figure|fig|table|chapter|section|page|圖|表)\s*[0-9]+|"
    r"https?://\S+|[0-9]+)$",
    re.IGNORECASE,
)
_NOISE_NAMES = frozenset(
    {
        "chapter",
        "fig",
        "figure",
        "isbn",
        "page",
        "section",
        "table",
        "作者",
        "內容",
        "參考資料",
        "摘要",
        "日期",
        "方法",
        "概念",
        "目錄",
        "系統",
        "資料",
        "版本",
        "章節",
        "練習",
    }
)


def build_concept_rule_decisions(
    package: Mapping[str, Any],
    *,
    normalized_source: Mapping[str, Any],
) -> dict[str, Any]:
    """從一份完全合格的 PASS handoff 建立可重播的 Concept 判斷。"""
    package_hash = (
        package.get("canonical_sha256")
        if isinstance(package, Mapping)
        and isinstance(package.get("canonical_sha256"), str)
        else None
    )
    material_id = (
        package.get("material_id")
        if isinstance(package, Mapping)
        and isinstance(package.get("material_id"), str)
        else None
    )
    result = {
        "schema_version": _SCHEMA_VERSION,
        "package_canonical_sha256": package_hash,
        "material_id": material_id,
        "decisions": [],
        "pending_questions": [],
        "retained_count": 0,
    }
    if not isinstance(package, Mapping) or not isinstance(
        normalized_source, Mapping
    ):
        return result
    if not is_handoff_consumer_eligible_package(
        package,
        normalized_source=normalized_source,
    ):
        return result

    candidates = package["candidates"]
    origins_by_id = {
        record["origin_id"]: record for record in package["origins"]
    }
    contexts_by_id = {
        record["context_id"]: record for record in package["contexts"]
    }
    evidence_by_id = {
        record["evidence_id"]: record
        for record in package["evidence_records"]
    }
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        normalized_name = _normalize_name(candidate["normalized_surface"])
        groups.setdefault(normalized_name, []).append(candidate)

    decisions = [
        _decision_for_group(
            sorted(group, key=lambda candidate: candidate["candidate_id"]),
            normalized_name=normalized_name,
            material_id=package["material_id"],
            package_hash=package["canonical_sha256"],
            normalized_source_binding=package["normalized_source_binding"],
            candidate_source_binding=package["candidate_source_binding"],
            origins_by_id=origins_by_id,
            contexts_by_id=contexts_by_id,
            evidence_by_id=evidence_by_id,
        )
        for normalized_name, group in sorted(groups.items())
    ]
    decisions.sort(key=lambda decision: decision["decision_id"])
    pending_questions = []
    for decision in decisions:
        if decision["outcome"] != "review":
            continue
        question = {
            "decision_id": decision["decision_id"],
            "candidate_id": decision["candidate_id"],
            "reason_codes": list(decision["reason_codes"]),
        }
        question["question_id"] = _stable_id(
            "concept-rule-question",
            {
                "decision_id": question["decision_id"],
                "reason_codes": question["reason_codes"],
            },
        )
        pending_questions.append(question)
    pending_questions.sort(key=lambda question: question["question_id"])
    result["decisions"] = decisions
    result["pending_questions"] = pending_questions
    result["retained_count"] = sum(
        decision["outcome"] == "retain" for decision in decisions
    )
    return result


def _decision_for_group(
    candidates: list[Mapping[str, Any]],
    *,
    normalized_name: str,
    material_id: str,
    package_hash: str,
    normalized_source_binding: Mapping[str, Any],
    candidate_source_binding: Mapping[str, Any],
    origins_by_id: Mapping[str, Mapping[str, Any]],
    contexts_by_id: Mapping[str, Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """在單一正規化名稱邊界內彙整證據，避免不同候選名稱互相污染。"""
    candidate_ids = sorted(
        candidate["candidate_id"] for candidate in candidates
    )
    evidence_ids = sorted(
        {
            evidence_id
            for candidate in candidates
            for evidence_id in candidate["evidence_ids"]
        }
    )
    evidences = [evidence_by_id[evidence_id] for evidence_id in evidence_ids]
    origin_ids = sorted(
        {
            origin_id
            for candidate in candidates
            for origin_id in candidate["origin_ids"]
        }
        | {
            origin_id
            for evidence in evidences
            for origin_id in evidence["origin_ids"]
        }
    )
    context_ids = sorted(
        {
            context_id
            for candidate in candidates
            for context_id in candidate["context_ids"]
        }
        | {
            context_id
            for evidence in evidences
            for context_id in evidence["context_ids"]
        }
    )
    semantic_evidences = [
        _semantic_evidence_view(
            evidence,
            normalized_name=normalized_name,
        )
        for evidence in evidences
    ]
    strong_evidence = [
        evidence
        for evidence in semantic_evidences
        if evidence["evidence_kind"] in _STRONG_EVIDENCE_KINDS
    ]
    definitions = [
        evidence
        for evidence in strong_evidence
        if evidence["evidence_kind"] == _DEFINITION_EVIDENCE_KIND
    ]
    has_conflict = any(
        candidate["support_summary"]["hard_negative_gate"]
        for candidate in candidates
    ) or not _definitions_are_compatible(definitions)

    if not _identifiable_name(normalized_name):
        route = "rejected_by_rules"
        outcome = "reject"
        reason_codes = ["name_not_identifiable"]
    elif not strong_evidence:
        route = "rejected_by_rules"
        outcome = "reject"
        reason_codes = ["weak_evidence_only"]
    elif has_conflict:
        route = "needs_local_model_review"
        outcome = "review"
        reason_codes = ["conflicting_strong_evidence"]
    elif not definitions:
        route = "needs_local_model_review"
        outcome = "review"
        reason_codes = ["strong_evidence_scope_unresolved"]
    else:
        route = "accepted_by_rules"
        outcome = "retain"
        reason_codes = ["strong_definition_evidence"]

    identity = {
        "candidate_ids": candidate_ids,
        "material_id": material_id,
        "normalized_name": normalized_name,
        "package_canonical_sha256": package_hash,
    }
    decision = {
        "decision_id": _stable_id("concept-rule-decision", identity),
        "candidate_id": candidate_ids[0],
        "candidate_ids": candidate_ids,
        "route": route,
        "outcome": outcome,
        "reason_codes": reason_codes,
        "evidence_ids": evidence_ids,
        "origin_locator_refs": [
            _origin_locator_ref(origins_by_id[origin_id])
            for origin_id in origin_ids
        ],
        "context_locator_refs": [
            _context_locator_ref(contexts_by_id[context_id])
            for context_id in context_ids
        ],
        "normalized_source_binding": deepcopy(
            dict(normalized_source_binding)
        ),
        "candidate_source_binding": deepcopy(
            dict(candidate_source_binding)
        ),
    }
    if outcome == "retain":
        teaching_scope = _teaching_scope(definitions)
        decision.update(
            {
                "concept_id": _stable_id(
                    "concept",
                    {
                        "material_id": material_id,
                        "normalized_name": normalized_name,
                    },
                ),
                "name": _preferred_name(candidates),
                "normalized_name": normalized_name,
                "teaching_scope": teaching_scope["statement"],
                "teaching_scope_evidence_ids": teaching_scope[
                    "evidence_ids"
                ],
            }
        )
    return decision


def _normalize_name(value: str) -> str:
    """將候選名稱正規化為同一份材料內精確分組使用的鍵值。"""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split())
    return normalized.strip(_EDGE_PUNCTUATION).casefold()


def _identifiable_name(value: str) -> bool:
    """排除無法作為 Concept 名稱的短值、純數字與結構性雜訊。"""
    if not 2 <= len(value) <= 80 or len(value.split()) > 8:
        return False
    if not any(character.isalpha() for character in value):
        return False
    if value in _NOISE_NAMES:
        return False
    return _STRUCTURAL_NOISE_PATTERN.fullmatch(value) is None


def _definitions_are_compatible(
    definitions: list[Mapping[str, Any]],
) -> bool:
    """只以包含關係聚合定義，避免因近似文字誤合併不同 Concept。"""
    statements = sorted(
        {
            _normalize_statement(evidence["normalized_statement"])
            for evidence in definitions
        }
    )
    return all(
        first in second or second in first
        for index, first in enumerate(statements)
        for second in statements[index + 1 :]
    )


def _normalize_statement(value: str) -> str:
    """正規化證據敘述，以支援可重播的精確比較與排序。"""
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _semantic_evidence_view(
    evidence: Mapping[str, Any],
    *,
    normalized_name: str,
) -> Mapping[str, Any]:
    """建立不回寫 sealed handoff 的 consumer-private 語意證據視圖。"""
    if evidence["evidence_kind"] != "candidate_literal":
        return evidence
    explanation = _direct_definition_explanation(
        evidence["statement"],
        normalized_name=normalized_name,
    )
    if explanation is None:
        return evidence
    derived = dict(evidence)
    derived["evidence_kind"] = _DEFINITION_EVIDENCE_KIND
    derived["statement"] = explanation
    derived["normalized_statement"] = explanation
    return derived


def _direct_definition_explanation(
    statement: str,
    *,
    normalized_name: str,
) -> str | None:
    """只接受名稱錨定、精確 connector 與完整 token 邊界的直接定義。"""
    normalized_statement = _normalize_statement(statement)
    if not normalized_statement.startswith(normalized_name):
        return None
    remainder = normalized_statement[len(normalized_name):]

    for connector in ("refers to", "means", "is"):
        prefix = f" {connector} "
        if remainder.startswith(prefix):
            return _bounded_explanation(remainder[len(prefix):])

    for connector in ("稱為", "是", "指"):
        prefix = f" {connector} "
        if remainder.startswith(prefix):
            return _bounded_explanation(remainder[len(prefix):])

    remainder = remainder.lstrip()
    if remainder.startswith(":"):
        return _bounded_explanation(remainder[1:].lstrip())
    return None


def _bounded_explanation(value: str) -> str | None:
    """修剪邊界標點，並拒絕沒有文字或數字內容的空泛說明。"""
    explanation = value.strip(_EDGE_PUNCTUATION)
    if not explanation or not any(
        character.isalnum() for character in explanation
    ):
        return None
    return explanation


def _teaching_scope(
    definitions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """從相容定義選出最完整的教學範圍並保留其證據參照。"""
    ordered = sorted(
        definitions,
        key=lambda evidence: (
            -len(_normalize_statement(evidence["statement"])),
            _normalize_statement(evidence["statement"]),
            evidence["evidence_id"],
        ),
    )
    statement = ordered[0]["statement"]
    evidence_ids = sorted(
        evidence["evidence_id"]
        for evidence in ordered
        if _normalize_statement(evidence["statement"])
        in _normalize_statement(statement)
    )
    return {"statement": statement, "evidence_ids": evidence_ids}


def _preferred_name(candidates: list[Mapping[str, Any]]) -> str:
    """以固定排序選出同一正規化名稱群組的代表名稱。"""
    return sorted(
        {candidate["surface"] for candidate in candidates},
        key=lambda value: (_normalize_name(value), value.casefold(), value),
    )[0]


def _origin_locator_ref(origin: Mapping[str, Any]) -> dict[str, Any]:
    """複製判斷結果追溯所需的最小來源定位資訊。"""
    return {
        "origin_id": origin["origin_id"],
        "candidate_id": origin["candidate_id"],
        "block_id": origin["block_id"],
        "layout_unit_id": origin["layout_unit_id"],
        "source_ref": origin["source_ref"],
        "pdf_page": origin["pdf_page"],
        "reading_order": origin["reading_order"],
        "bbox": deepcopy(origin["bbox"]),
        "literal_span": deepcopy(origin["literal_span"]),
    }


def _context_locator_ref(context: Mapping[str, Any]) -> dict[str, Any]:
    """複製判斷結果追溯所需的最小上下文定位資訊。"""
    return {
        "context_id": context["context_id"],
        "layout_unit_refs": deepcopy(context["layout_unit_refs"]),
        "start_locator": deepcopy(context["start_locator"]),
        "end_locator": deepcopy(context["end_locator"]),
    }


def _stable_id(prefix: str, value: Any) -> str:
    """雜湊 canonical bytes 產生穩定 ID，讓 payload 漂移必須改變身分。"""
    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return f"{prefix}:{digest}"

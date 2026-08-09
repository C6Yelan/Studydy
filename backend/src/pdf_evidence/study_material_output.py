from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any

from .concept_content import (
    CONCEPT_CONTENT_PROMPT_VERSION,
    CONCEPT_CONTENT_SCHEMA,
    CONCEPT_KEYWORDS_SCHEMA,
    MAX_RELATION_STATEMENT_CHARACTERS,
    MAX_SUMMARY_CHARACTERS,
    RELATION_CLUE_KINDS,
    RELATION_DIRECTIONS,
)
from .concept_deduplication import CONCEPT_GROUP_SCHEMA
from .page_structure import validate_page_structure


STUDY_MATERIAL_OUTPUT_SCHEMA = "study-material-output/v2"
EVIDENCE_REFERENCE_SCHEMA = "evidence-reference/v1"
FORMAL_PROVIDER_DEFERRED = "FORMAL_PROVIDER_DEFERRED"
CONCEPT_CONTEXT_UNAVAILABLE = "CONCEPT_CONTEXT_UNAVAILABLE"

ROOT_FIELDS = frozenset(
    "schema output_id development_only handoff_id produced_at material_ref pages "
    "concepts evidence_index summaries keywords relation_clues known_limitations "
    "provenance processing quality decision reason_code".split()
)
PROVENANCE_FIELDS = frozenset(
    "page_evidence page_structure concepts content".split()
)
PAGE_FIELDS = frozenset(
    "page_ref page_number page_evidence_ref page_structure_ref".split()
)
CONCEPT_FIELDS = frozenset(
    "concept_id normalized_name members processing quality decision reason_code".split()
)
MEMBER_FIELDS = frozenset(
    "candidate_id page_ref page_number name definition scope evidence_ids".split()
)
EVIDENCE_FIELDS = frozenset(
    "evidence_id material_ref page_ref page_number element_id region".split()
)
SUMMARY_FIELDS = frozenset(
    "source_concept_ids summary summary_evidence_ids processing quality decision reason_code".split()
)
KEYWORD_FIELDS = frozenset(
    "keyword concept_id evidence_ids processing quality decision reason_code".split()
)
CLUE_FIELDS = frozenset(
    "kind source_concept_id target_concept_id statement direction_hint evidence_ids".split()
)
CONCEPT_STATUSES = (
    ("succeeded", "accepted", "retain", "CONCEPT_GROUP_ACCEPTED"),
    ("succeeded", "needs_review", "review", "CONCEPT_GROUP_SEMANTIC_CONFLICT"),
)
KEYWORD_STATUSES = (
    ("succeeded", "accepted", "retain", "CONCEPT_KEYWORDS_ACCEPTED"),
    ("succeeded", "needs_review", "review", "CONCEPT_KEYWORDS_LIMIT_APPLIED"),
    ("succeeded", "needs_review", "review", "CONCEPT_KEYWORDS_SOURCE_NEEDS_REVIEW"),
)
PARTIAL_STATUS = ("partial", "needs_review", "review", "DEVELOPMENT_FULL_DOCUMENT_PARTIAL")
DEFERRED_STATUS = ("succeeded", "needs_review", "review", "DEVELOPMENT_OUTPUT_NEEDS_REVIEW")
COMPLETED_STATUS = ("succeeded", "accepted", "retain", "DEVELOPMENT_OUTPUT_ACCEPTED")


def _nonempty_string(value: Any) -> bool:
    """判斷值是否為去除空白後仍有內容的字串。"""
    return isinstance(value, str) and bool(value.strip())


def _valid_sha256_ref(value: Any, prefix: str) -> bool:
    """檢查 reference 是否由指定前綴與小寫 SHA-256 組成。"""
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value.removeprefix(prefix)
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _canonical_sha256(value: Any) -> str | None:
    """以固定 JSON 規則計算 SHA-256；不可編碼時回傳 None。"""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _failure(reason_code: str) -> dict[str, Any]:
    """建立不包含任何未驗證語意內容的失敗結果。"""
    return {
        "schema": STUDY_MATERIAL_OUTPUT_SCHEMA,
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }


def _unique_strings(values: Any) -> bool:
    """驗證字串清單內容與唯一性。"""
    return (
        isinstance(values, list)
        and bool(values)
        and all(_nonempty_string(value) for value in values)
        and len(values) == len(set(values))
    )


def _status(item: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """讀取共用的 processing、quality、decision 與 reason。"""
    return (
        item.get("processing"),
        item.get("quality"),
        item.get("decision"),
        item.get("reason_code"),
    )


def _valid_region(region: Any) -> bool:
    """檢查 Evidence locator 的座標系與 bbox。"""
    if not isinstance(region, dict) or set(region) != {
        "coordinate_space",
        "bbox",
    }:
        return False
    bbox = region["bbox"]
    if (
        region["coordinate_space"] != "unrotated_page_points"
        or not isinstance(bbox, list)
        or len(bbox) != 4
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in bbox
        )
    ):
        return False
    x0, y0, x1, y1 = bbox
    return x1 > x0 and y1 > y0


def _valid_provenance(provenance: Any) -> bool:
    """確認 content provenance 明確綁定目前 schema 與 prompt 版本。"""
    if (
        not isinstance(provenance, dict)
        or set(provenance) != PROVENANCE_FIELDS
        or any(not _nonempty_string(provenance[field]) for field in PROVENANCE_FIELDS)
    ):
        return False
    content_versions = provenance["content"].split(";")
    return (
        CONCEPT_CONTENT_SCHEMA in content_versions
        and CONCEPT_CONTENT_PROMPT_VERSION in content_versions
    )


def _root_status(limitation_reasons: set[str]) -> tuple[str, str, str, str] | None:
    """依已知限制決定 completed、deferred 或 partial 根層狀態。"""
    if not limitation_reasons:
        return COMPLETED_STATUS
    if FORMAL_PROVIDER_DEFERRED not in limitation_reasons:
        return None
    if CONCEPT_CONTEXT_UNAVAILABLE in limitation_reasons:
        return PARTIAL_STATUS
    return DEFERRED_STATUS


def _pack_pages(
    page_evidence_items: Any, page_structure_items: Any
) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]] | None:
    """由已驗證的一對一頁面產物建立最小 locator。"""
    if (
        not isinstance(page_evidence_items, list)
        or not page_evidence_items
        or not isinstance(page_structure_items, list)
        or len(page_evidence_items) != len(page_structure_items)
    ):
        return None
    structures = {}
    for structure in page_structure_items:
        if not isinstance(structure, dict) or not _nonempty_string(
            structure.get("page_ref")
        ):
            return None
        if structure["page_ref"] in structures:
            return None
        structures[structure["page_ref"]] = structure

    material_ref = None
    pages = []
    sources = {}
    for evidence in page_evidence_items:
        if not isinstance(evidence, dict):
            return None
        page_ref = evidence.get("page_ref")
        structure = structures.get(page_ref) if _nonempty_string(page_ref) else None
        if structure is None or validate_page_structure(structure, evidence) is not None:
            return None
        current_material_ref = evidence["material_ref"]
        if material_ref is None:
            material_ref = current_material_ref
        elif current_material_ref != material_ref:
            return None
        structure_sha256 = _canonical_sha256(structure)
        if structure_sha256 is None or page_ref in sources:
            return None
        pages.append(
            {
                "page_ref": page_ref,
                "page_number": evidence["page_number"],
                "page_evidence_ref": evidence["evidence_ref"],
                "page_structure_ref": f"page-structure:sha256:{structure_sha256}",
            }
        )
        sources[page_ref] = {
            "evidence": evidence,
            "elements": {
                element["id"]: element for element in structure["elements"]
            },
        }
    if set(structures) != set(sources):
        return None
    pages.sort(key=lambda page: page["page_number"])
    return material_ref, pages, sources


def _pack_evidence(reference: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    """確認上游 Evidence 指向同頁實際 element，並擷取公開 locator。"""
    fields = {
        "evidence_id",
        "schema",
        "material_ref",
        "page_ref",
        "page_number",
        "input_evidence_ref",
        "element_id",
        "region",
    }
    if not isinstance(reference, dict) or set(reference) != fields:
        return None
    evidence = source["evidence"]
    element_id = reference["element_id"]
    element = source["elements"].get(element_id) if _nonempty_string(element_id) else None
    if (
        reference["schema"] != EVIDENCE_REFERENCE_SCHEMA
        or element is None
        or reference["material_ref"] != evidence["material_ref"]
        or reference["page_ref"] != evidence["page_ref"]
        or reference["page_number"] != evidence["page_number"]
        or reference["input_evidence_ref"] != evidence["evidence_ref"]
        or reference["region"]
        != {
            "coordinate_space": "unrotated_page_points",
            "bbox": element["bbox"],
        }
    ):
        return None
    return {field: deepcopy(reference[field]) for field in EVIDENCE_FIELDS}


def _pack_concepts(
    concept_groups: Any,
    material_ref: str,
    page_sources: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """整理 retained/review Concept 與其 Evidence union。"""
    if not isinstance(concept_groups, list) or not concept_groups:
        return None
    concepts = []
    evidence_by_id = {}
    try:
        for group in concept_groups:
            if (
                group["schema"] != CONCEPT_GROUP_SCHEMA
                or group["material_ref"] != material_ref
                or _status(group) not in CONCEPT_STATUSES
                or not group["members"]
            ):
                return None
            members = []
            for member in group["members"]:
                source_page = member["source_page"]
                source = page_sources.get(source_page["page_ref"])
                if source is None or source_page != {
                    "material_ref": source["evidence"]["material_ref"],
                    "page_ref": source["evidence"]["page_ref"],
                    "page_number": source["evidence"]["page_number"],
                }:
                    return None
                evidence_ids = []
                for reference in member["evidence"]:
                    locator = _pack_evidence(reference, source)
                    if locator is None:
                        return None
                    evidence_id = locator["evidence_id"]
                    previous = evidence_by_id.get(evidence_id)
                    if previous is not None and previous != locator:
                        return None
                    evidence_by_id[evidence_id] = locator
                    evidence_ids.append(evidence_id)
                packed_member = {
                    field: member[field]
                    for field in ("candidate_id", "name", "definition", "scope")
                }
                packed_member.update(
                    page_ref=source_page["page_ref"],
                    page_number=source_page["page_number"],
                    evidence_ids=evidence_ids,
                )
                members.append(packed_member)
            members.sort(key=lambda member: member["candidate_id"])
            concept = {
                field: group[field]
                for field in (
                    "normalized_name",
                    "processing",
                    "quality",
                    "decision",
                    "reason_code",
                )
            }
            concept.update(concept_id=group["group_id"], members=members)
            concepts.append(concept)
    except (KeyError, TypeError):
        return None
    concepts.sort(key=lambda concept: concept["concept_id"])
    evidence_index = sorted(
        evidence_by_id.values(), key=lambda item: item["evidence_id"]
    )
    return concepts, evidence_index


def _pack_content(concept_content_items: Any, material_ref: str) -> tuple[list, list] | None:
    """分離已驗證 content 的 Summary 與 reviewable clues。"""
    if not isinstance(concept_content_items, list) or not concept_content_items:
        return None
    summaries = []
    clues = []
    try:
        for item in concept_content_items:
            if (
                item["schema"] != CONCEPT_CONTENT_SCHEMA
                or item["development_only"] is not True
                or item["material_ref"] != material_ref
                or _status(item)[:3] != ("succeeded", "needs_review", "review")
            ):
                return None
            summary = {
                field: deepcopy(item[field])
                for field in SUMMARY_FIELDS
                if field != "source_concept_ids"
            }
            summary["source_concept_ids"] = sorted(item["source_group_ids"])
            summaries.append(summary)
            for clue in deepcopy(item["relation_clues"]):
                clue["source_concept_id"] = clue.pop("source_group_id")
                clue["target_concept_id"] = clue.pop("target_group_id")
                clues.append(clue)
        summaries.sort(key=lambda item: item["source_concept_ids"])
        clues.sort(
            key=lambda clue: (
                clue.get("source_concept_id"),
                clue.get("target_concept_id"),
                clue.get("kind"),
                clue.get("statement"),
                clue.get("direction_hint"),
            )
        )
    except (KeyError, TypeError):
        return None
    return summaries, clues


def _pack_keywords(concept_keyword_items: Any, material_ref: str) -> list | None:
    """把 Keyword batches 整理成下一階段可直接讀取的清單。"""
    if not isinstance(concept_keyword_items, list) or not concept_keyword_items:
        return None
    keywords = []
    try:
        for item in concept_keyword_items:
            if (
                item["schema"] != CONCEPT_KEYWORDS_SCHEMA
                or item["material_ref"] != material_ref
                or _status(item) not in KEYWORD_STATUSES
                or not item["keywords"]
            ):
                return None
            for keyword in item["keywords"]:
                packed_keyword = deepcopy(keyword)
                packed_keyword["concept_id"] = packed_keyword.pop("group_id")
                packed_keyword.update(
                    {
                        field: item[field]
                        for field in (
                            "processing",
                            "quality",
                            "decision",
                            "reason_code",
                        )
                    }
                )
                keywords.append(packed_keyword)
        keywords.sort(key=lambda item: item["concept_id"])
    except (KeyError, TypeError):
        return None
    return keywords


def _validate_pages(output: dict[str, Any]) -> tuple[dict[str, dict], str | None]:
    """驗證 packaged pages 的 shape、排序與唯一 identity。"""
    pages = output["pages"]
    if not isinstance(pages, list) or not pages:
        return {}, "STUDY_MATERIAL_OUTPUT_PAGE_INVALID"
    page_by_ref = {}
    page_numbers = set()
    evidence_refs = set()
    structure_refs = set()
    for page in pages:
        if not isinstance(page, dict) or set(page) != PAGE_FIELDS:
            return {}, "STUDY_MATERIAL_OUTPUT_PAGE_INVALID"
        page_ref = page["page_ref"]
        page_number = page["page_number"]
        if (
            not _valid_sha256_ref(page_ref, "page:sha256:")
            or isinstance(page_number, bool)
            or not isinstance(page_number, int)
            or page_number < 1
            or not _valid_sha256_ref(page["page_evidence_ref"], "evidence:sha256:")
            or not _valid_sha256_ref(
                page["page_structure_ref"], "page-structure:sha256:"
            )
            or page_ref in page_by_ref
            or page_number in page_numbers
            or page["page_evidence_ref"] in evidence_refs
            or page["page_structure_ref"] in structure_refs
        ):
            return {}, "STUDY_MATERIAL_OUTPUT_PAGE_INVALID"
        page_by_ref[page_ref] = page
        page_numbers.add(page_number)
        evidence_refs.add(page["page_evidence_ref"])
        structure_refs.add(page["page_structure_ref"])
    if pages != sorted(pages, key=lambda page: page["page_number"]):
        return {}, "STUDY_MATERIAL_OUTPUT_PAGE_INVALID"
    return page_by_ref, None


def _validate_evidence(
    output: dict[str, Any], page_by_ref: dict[str, dict]
) -> tuple[dict[str, dict], str | None]:
    """驗證 Evidence index 的 locator、同教材 identity 與唯一性。"""
    items = output["evidence_index"]
    if not isinstance(items, list) or not items:
        return {}, "STUDY_MATERIAL_OUTPUT_EVIDENCE_INVALID"
    evidence_by_id = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != EVIDENCE_FIELDS:
            return {}, "STUDY_MATERIAL_OUTPUT_EVIDENCE_INVALID"
        page_ref = item.get("page_ref")
        if not _nonempty_string(page_ref):
            return {}, "STUDY_MATERIAL_OUTPUT_EVIDENCE_INVALID"
        page = page_by_ref.get(page_ref)
        evidence_id = item.get("evidence_id")
        if (
            page is None
            or not _valid_sha256_ref(evidence_id, "evidence-reference:sha256:")
            or evidence_id in evidence_by_id
            or item["material_ref"] != output["material_ref"]
            or item["page_number"] != page["page_number"]
            or not _nonempty_string(item["element_id"])
            or not _valid_region(item["region"])
        ):
            return {}, "STUDY_MATERIAL_OUTPUT_EVIDENCE_INVALID"
        evidence_by_id[evidence_id] = item
    return evidence_by_id, None


def _validate_concepts(
    output: dict[str, Any],
    page_by_ref: dict[str, dict],
    evidence_by_id: dict[str, dict],
) -> tuple[dict[str, set[str]], dict[str, str], set[str], str | None]:
    """驗證 Concept members、Evidence references 與 index 無 orphan。"""
    concepts = output["concepts"]
    if not isinstance(concepts, list) or not concepts:
        return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_CONCEPT_INVALID"
    group_evidence = {}
    names_by_group = {}
    candidate_ids = set()
    used_evidence_ids = set()
    concept_page_refs = set()
    for concept in concepts:
        if (
            not isinstance(concept, dict)
            or set(concept) != CONCEPT_FIELDS
            or not _valid_sha256_ref(concept["concept_id"], "concept-group:sha256:")
            or concept["concept_id"] in group_evidence
            or not _nonempty_string(concept["normalized_name"])
            or _status(concept) not in CONCEPT_STATUSES
            or not isinstance(concept["members"], list)
            or not concept["members"]
        ):
            return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_CONCEPT_INVALID"
        current_evidence_ids = set()
        for member in concept["members"]:
            if not isinstance(member, dict) or set(member) != MEMBER_FIELDS:
                return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_CONCEPT_INVALID"
            page_ref = member["page_ref"]
            if not _nonempty_string(page_ref):
                return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_CONCEPT_INVALID"
            page = page_by_ref.get(page_ref)
            evidence_ids = member["evidence_ids"]
            if not _unique_strings(evidence_ids):
                return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_DUPLICATE_INVALID"
            if (
                page is None
                or member["page_number"] != page["page_number"]
                or not _valid_sha256_ref(
                    member["candidate_id"], "concept-candidate:sha256:"
                )
                or member["candidate_id"] in candidate_ids
                or any(
                    not _nonempty_string(member[field])
                    for field in ("name", "definition", "scope")
                )
            ):
                return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_CONCEPT_INVALID"
            for evidence_id in evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if (
                    evidence is None
                    or evidence["page_ref"] != member["page_ref"]
                    or evidence["page_number"] != member["page_number"]
                ):
                    return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
            candidate_ids.add(member["candidate_id"])
            current_evidence_ids.update(evidence_ids)
            used_evidence_ids.update(evidence_ids)
            concept_page_refs.add(member["page_ref"])
        group_evidence[concept["concept_id"]] = current_evidence_ids
        names_by_group[concept["concept_id"]] = concept["normalized_name"]
    if used_evidence_ids != set(evidence_by_id):
        return {}, {}, set(), "STUDY_MATERIAL_OUTPUT_ORPHAN_EVIDENCE"
    return group_evidence, names_by_group, concept_page_refs, None


def _known_evidence_ids(
    evidence_ids: Any,
    evidence_by_id: dict[str, dict],
    allowed_ids: set[str],
) -> bool:
    """確認引用清單唯一，且每筆都在允許的 Evidence 集合。"""
    return _unique_strings(evidence_ids) and all(
        evidence_id in evidence_by_id and evidence_id in allowed_ids
        for evidence_id in evidence_ids
    )


def _validate_content(
    output: dict[str, Any],
    evidence_by_id: dict[str, dict],
    group_evidence: dict[str, set[str]],
    names_by_group: dict[str, str],
) -> str | None:
    """驗證 Summary、Keyword 與 clues 的完整 coverage 和 grounding。"""
    summaries = output["summaries"]
    if not isinstance(summaries, list) or not summaries:
        return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
    covered_groups = set()
    for summary in summaries:
        if not isinstance(summary, dict) or set(summary) != SUMMARY_FIELDS:
            return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
        group_ids = summary["source_concept_ids"]
        text = summary["summary"]
        if (
            not _unique_strings(group_ids)
            or any(group_id not in group_evidence for group_id in group_ids)
            or covered_groups.intersection(group_ids)
            or not _nonempty_string(text)
            or len(text) > MAX_SUMMARY_CHARACTERS
            or _status(summary)[:3] != ("succeeded", "needs_review", "review")
            or summary["reason_code"]
            not in {
                "CONCEPT_CONTENT_NEEDS_REVIEW",
                "CONCEPT_CONTENT_NO_RELATION_CLUES",
            }
        ):
            return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
        allowed_ids = set().union(*(group_evidence[group_id] for group_id in group_ids))
        if not _known_evidence_ids(
            summary["summary_evidence_ids"], evidence_by_id, allowed_ids
        ):
            return "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
        covered_groups.update(group_ids)
    if covered_groups != set(group_evidence):
        return "STUDY_MATERIAL_OUTPUT_COVERAGE_INVALID"
    keywords = output["keywords"]
    if not isinstance(keywords, list) or not keywords:
        return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
    keyword_groups = set()
    for keyword in keywords:
        if not isinstance(keyword, dict) or set(keyword) != KEYWORD_FIELDS:
            return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
        group_id = keyword["concept_id"]
        if (
            not _nonempty_string(group_id)
            or group_id not in names_by_group
            or group_id in keyword_groups
            or keyword["keyword"] != names_by_group[group_id]
            or _status(keyword) not in KEYWORD_STATUSES
            or not _known_evidence_ids(
                keyword["evidence_ids"],
                evidence_by_id,
                group_evidence[group_id],
            )
        ):
            return "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
        keyword_groups.add(group_id)
    if keyword_groups != set(group_evidence):
        return "STUDY_MATERIAL_OUTPUT_COVERAGE_INVALID"
    clues = output["relation_clues"]
    if not isinstance(clues, list):
        return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
    clue_keys = set()
    for clue in clues:
        if not isinstance(clue, dict) or set(clue) != CLUE_FIELDS:
            return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
        source_id = clue["source_concept_id"]
        target_id = clue["target_concept_id"]
        statement = clue["statement"]
        evidence_ids = clue["evidence_ids"]
        if (
            clue["kind"] not in RELATION_CLUE_KINDS
            or clue["direction_hint"] not in RELATION_DIRECTIONS
            or not _nonempty_string(source_id)
            or not _nonempty_string(target_id)
            or source_id not in group_evidence
            or target_id not in group_evidence
            or source_id == target_id
            or not _nonempty_string(statement)
            or statement != statement.strip()
            or len(statement) > MAX_RELATION_STATEMENT_CHARACTERS
            or not _known_evidence_ids(
                evidence_ids,
                evidence_by_id,
                group_evidence[source_id] | group_evidence[target_id],
            )
        ):
            return "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
        cited_ids = set(evidence_ids)
        if (
            not cited_ids.intersection(group_evidence[source_id])
            or not cited_ids.intersection(group_evidence[target_id])
        ):
            return "STUDY_MATERIAL_OUTPUT_REFERENCE_INVALID"
        clue_key = (
            clue["kind"],
            source_id,
            target_id,
            statement,
            clue["direction_hint"],
        )
        if clue_key in clue_keys:
            return "STUDY_MATERIAL_OUTPUT_CONTENT_INVALID"
        clue_keys.add(clue_key)
    return None


def _validate_limitations(
    output: dict[str, Any],
    page_by_ref: dict[str, dict],
    concept_page_refs: set[str],
) -> str | None:
    """驗證限制 references，並確保根層狀態沒有假成功。"""
    limitations = output["known_limitations"]
    if not isinstance(limitations, list):
        return "STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID"
    reasons = set()
    unavailable_pages = set()
    for item in limitations:
        if not isinstance(item, dict) or set(item) != {
            "reason_code",
            "affected_page_refs",
        }:
            return "STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID"
        reason = item["reason_code"]
        page_refs = item["affected_page_refs"]
        if (
            not _nonempty_string(reason)
            or reason not in {CONCEPT_CONTEXT_UNAVAILABLE, FORMAL_PROVIDER_DEFERRED}
            or reason in reasons
            or not _unique_strings(page_refs)
            or page_refs != sorted(page_refs)
            or any(page_ref not in page_by_ref for page_ref in page_refs)
        ):
            return "STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID"
        reasons.add(reason)
        if reason == CONCEPT_CONTEXT_UNAVAILABLE:
            unavailable_pages.update(page_refs)
    expected_status = _root_status(reasons)
    if (
        limitations != sorted(limitations, key=lambda item: item["reason_code"])
        or unavailable_pages.intersection(concept_page_refs)
        or expected_status is None
        or _status(output) != expected_status
    ):
        return "STUDY_MATERIAL_OUTPUT_STATUS_INVALID"
    return None


def validate_study_material_output(output: Any) -> str | None:
    """驗證 canonical ID 及 packaged output 的完整 reference Gate。"""
    if not isinstance(output, dict) or set(output) != ROOT_FIELDS:
        return "STUDY_MATERIAL_OUTPUT_ROOT_INVALID"
    provenance = output["provenance"]
    if (
        output["schema"] != STUDY_MATERIAL_OUTPUT_SCHEMA
        or output["development_only"] is not True
        or not _nonempty_string(output["handoff_id"])
        or not _nonempty_string(output["produced_at"])
        or not _valid_sha256_ref(output["material_ref"], "material:sha256:")
        or not _valid_provenance(provenance)
    ):
        return "STUDY_MATERIAL_OUTPUT_ROOT_INVALID"
    content = {key: value for key, value in output.items() if key != "output_id"}
    content_sha256 = _canonical_sha256(content)
    if output["output_id"] != f"study-material-output:sha256:{content_sha256}":
        return "STUDY_MATERIAL_OUTPUT_ID_INVALID"

    page_by_ref, reason = _validate_pages(output)
    if reason is not None:
        return reason
    evidence_by_id, reason = _validate_evidence(output, page_by_ref)
    if reason is not None:
        return reason
    group_evidence, names_by_group, concept_pages, reason = _validate_concepts(
        output, page_by_ref, evidence_by_id
    )
    if reason is not None:
        return reason
    reason = _validate_content(
        output, evidence_by_id, group_evidence, names_by_group
    )
    if reason is not None:
        return reason
    return _validate_limitations(output, page_by_ref, concept_pages)


def build_study_material_output(
    page_evidence_items: Any,
    page_structure_items: Any,
    concept_groups: Any,
    concept_content_items: Any,
    concept_keyword_items: Any,
    *,
    handoff_id: Any,
    produced_at: Any,
    page_limitations: Any,
    provenance: Any,
) -> dict[str, Any]:
    """將既有已驗證產物包成可由下一階段獨立讀取的輸出。"""
    if not _nonempty_string(handoff_id) or not _nonempty_string(produced_at):
        return _failure("STUDY_MATERIAL_OUTPUT_LINEAGE_INVALID")
    if not _valid_provenance(provenance):
        return _failure("STUDY_MATERIAL_OUTPUT_PROVENANCE_INVALID")
    if isinstance(page_evidence_items, list):
        material_refs = [
            item.get("material_ref")
            for item in page_evidence_items
            if isinstance(item, dict)
        ]
        if all(_nonempty_string(value) for value in material_refs) and len(
            set(material_refs)
        ) > 1:
            return _failure("STUDY_MATERIAL_OUTPUT_IDENTITY_INVALID")

    packed_pages = _pack_pages(page_evidence_items, page_structure_items)
    if packed_pages is None:
        return _failure("STUDY_MATERIAL_OUTPUT_PAGE_INPUT_INVALID")
    material_ref, pages, page_sources = packed_pages
    packed_concepts = _pack_concepts(concept_groups, material_ref, page_sources)
    if packed_concepts is None:
        return _failure("STUDY_MATERIAL_OUTPUT_CONCEPT_INPUT_INVALID")
    concepts, evidence_index = packed_concepts
    packed_content = _pack_content(concept_content_items, material_ref)
    if packed_content is None:
        return _failure("STUDY_MATERIAL_OUTPUT_CONTENT_INPUT_INVALID")
    summaries, relation_clues = packed_content
    keywords = _pack_keywords(concept_keyword_items, material_ref)
    if keywords is None:
        return _failure("STUDY_MATERIAL_OUTPUT_KEYWORD_INPUT_INVALID")
    if not isinstance(page_limitations, list):
        return _failure("STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID")
    try:
        if any(
            set(item) != {"reason_code", "affected_page_refs"}
            for item in page_limitations
        ):
            return _failure("STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID")
        known_limitations = [
            {
                "reason_code": item["reason_code"],
                "affected_page_refs": sorted(item["affected_page_refs"]),
            }
            for item in page_limitations
        ]
        known_limitations.sort(key=lambda item: item["reason_code"])
    except (KeyError, TypeError):
        return _failure("STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID")
    try:
        root_status = _root_status(
            {item["reason_code"] for item in known_limitations}
        )
    except TypeError:
        return _failure("STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID")
    if root_status is None:
        return _failure("STUDY_MATERIAL_OUTPUT_LIMITATION_INVALID")

    processing, quality, decision, reason_code = root_status
    content = {
        "schema": STUDY_MATERIAL_OUTPUT_SCHEMA,
        "development_only": True,
        "handoff_id": handoff_id,
        "produced_at": produced_at,
        "material_ref": material_ref,
        "pages": pages,
        "concepts": concepts,
        "evidence_index": evidence_index,
        "summaries": summaries,
        "keywords": keywords,
        "relation_clues": relation_clues,
        "known_limitations": known_limitations,
        "provenance": deepcopy(provenance),
        "processing": processing,
        "quality": quality,
        "decision": decision,
        "reason_code": reason_code,
    }
    content_sha256 = _canonical_sha256(content)
    if content_sha256 is None:
        return _failure("STUDY_MATERIAL_OUTPUT_CANONICALIZATION_FAILED")
    output = {
        "output_id": f"study-material-output:sha256:{content_sha256}",
        **content,
    }
    reason = validate_study_material_output(output)
    return output if reason is None else _failure(reason)

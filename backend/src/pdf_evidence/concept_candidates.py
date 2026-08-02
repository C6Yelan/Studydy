from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .page_structure import validate_page_structure


CONCEPT_CONTEXT_SCHEMA = "s1-concept-context/v1"
EVIDENCE_REFERENCE_SCHEMA = "s1-evidence-reference/v1"
CONCEPT_CANDIDATE_SCHEMA = "s1-concept-candidate/v1"
CONCEPT_PROMPT_VERSION = "s1-concept-candidate-prompt/v1"
CONCEPT_PROMPT = (
    "Use only the supplied single-page concept context. Return one JSON object with exactly "
    "name, definition, scope, and evidence_ids. Each text field must be nonempty. evidence_ids "
    "must be a nonempty subset of the supplied IDs and must include the heading anchor evidence. "
    "Do not invent facts, relations, a Knowledge Map, or a Learning Path."
)
CONCEPT_PROMPT_SHA256 = hashlib.sha256(CONCEPT_PROMPT.encode("utf-8")).hexdigest()
CONCEPT_BODY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "definition", "scope", "evidence_ids"],
    "properties": {
        "name": {"type": "string", "pattern": r".*\S.*"},
        "definition": {"type": "string", "pattern": r".*\S.*"},
        "scope": {"type": "string", "pattern": r".*\S.*"},
        "evidence_ids": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "pattern": r".*\S.*"},
        },
    },
}


def _canonical_sha256(value: Any) -> str | None:
    """用固定 JSON 表示計算 SHA-256，無法序列化時回傳 None。"""
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


def _nonempty_text(value: Any) -> bool:
    """判斷值是否為去除空白後仍有內容的字串。"""
    return isinstance(value, str) and bool(value.strip())


def _valid_sha256(value: Any) -> bool:
    """判斷值是否為小寫十六進位的標準 SHA-256。"""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _make_evidence_reference(
    page_structure: dict[str, Any], element: dict[str, Any]
) -> dict[str, Any]:
    """由已驗證 element 建立可回到同頁區域的穩定 Evidence reference。"""
    reference = {
        "schema": EVIDENCE_REFERENCE_SCHEMA,
        "material_ref": page_structure["material_ref"],
        "page_ref": page_structure["page_ref"],
        "page_number": page_structure["page_number"],
        "input_evidence_ref": page_structure["input_evidence_ref"],
        "element_id": element["id"],
        "region": {
            "coordinate_space": page_structure["coordinate_space"],
            "bbox": list(element["bbox"]),
        },
    }
    evidence_sha256 = _canonical_sha256(reference)
    return {
        "evidence_id": f"evidence-reference:sha256:{evidence_sha256}",
        **reference,
    }


def build_concept_context(
    page_structure: Any,
    page_evidence: Any,
    page_alignment: Any,
    anchor_element_id: Any,
) -> dict[str, Any] | None:
    """由 accepted 單頁 alignment 建立一個 heading section 的最小 context。"""
    if validate_page_structure(page_structure, page_evidence) is not None:
        return None
    if not _nonempty_text(anchor_element_id):
        return None
    alignment_fields = {
        "schema",
        "identity",
        "input_binding",
        "processing",
        "quality",
        "decision",
        "reason_code",
        "findings",
    }
    if not isinstance(page_alignment, dict) or set(page_alignment) != alignment_fields:
        return None
    alignment_input_binding = page_alignment["input_binding"]
    if (
        not isinstance(alignment_input_binding, dict)
        or set(alignment_input_binding)
        != {"evidence_ref", "page_structure_sha256", "native_sha256"}
    ):
        return None
    identity = {
        "material_ref": page_structure["material_ref"],
        "page_ref": page_structure["page_ref"],
        "page_number": page_structure["page_number"],
    }
    page_structure_sha256 = _canonical_sha256(page_structure)
    expected_input_binding = {
        "evidence_ref": page_evidence["evidence_ref"],
        "page_structure_sha256": page_structure_sha256,
        "native_sha256": alignment_input_binding["native_sha256"],
    }
    if (
        page_alignment["schema"] != "s1-page-alignment/v1"
        or page_alignment["identity"] != identity
        or page_alignment["input_binding"] != expected_input_binding
        or not _valid_sha256(expected_input_binding["native_sha256"])
        or page_alignment["processing"] != "succeeded"
        or page_alignment["quality"] != "accepted"
        or page_alignment["decision"] != "retain"
        or page_alignment["reason_code"] != "ALIGNMENT_ACCEPTED"
        or page_alignment["findings"] != []
    ):
        return None

    elements_by_id = {
        element["id"]: element for element in page_structure["elements"]
    }
    anchor = elements_by_id.get(anchor_element_id)
    if anchor is None or anchor["type"] != "heading":
        return None
    try:
        anchor_index = page_structure["reading_order"].index(anchor_element_id)
    except ValueError:
        return None
    selected_elements = [anchor]
    for element_id in page_structure["reading_order"][anchor_index + 1 :]:
        element = elements_by_id[element_id]
        if element["type"] == "heading":
            break
        selected_elements.append(element)
    if len(selected_elements) < 2:
        return None

    context_elements = [
        {
            "element_id": element["id"],
            "type": element["type"],
            "text": element["text"],
        }
        for element in selected_elements
    ]
    evidence = [
        _make_evidence_reference(page_structure, element)
        for element in selected_elements
    ]
    return {
        "schema": CONCEPT_CONTEXT_SCHEMA,
        "identity": identity,
        "input_binding": {
            "evidence_ref": page_evidence["evidence_ref"],
            "page_structure_sha256": page_structure_sha256,
            "alignment_sha256": _canonical_sha256(page_alignment),
        },
        "anchor_element_id": anchor_element_id,
        "elements": context_elements,
        "evidence": evidence,
    }


def _candidate_failure(reason_code: str) -> dict[str, Any]:
    """建立不含未驗證語意內容的 provisional candidate 失敗結果。"""
    return {
        "schema": CONCEPT_CANDIDATE_SCHEMA,
        "development_only": True,
        "processing": "failed",
        "quality": "unsupported",
        "decision": "reject",
        "reason_code": reason_code,
    }


def _candidate_core(candidate: dict[str, Any]) -> dict[str, Any]:
    """擷取 candidate ID 綁定且人工裁決不可改寫的內容與 lineage。"""
    return {
        "schema": candidate["schema"],
        "development_only": candidate["development_only"],
        "handoff_id": candidate["handoff_id"],
        "identity": candidate["identity"],
        "sol_identity": candidate["sol_identity"],
        "prompt_identity": candidate["prompt_identity"],
        "context_binding": candidate["context_binding"],
        "name": candidate["name"],
        "definition": candidate["definition"],
        "scope": candidate["scope"],
        "evidence": candidate["evidence"],
    }


def build_provisional_concept_candidate(
    context: Any,
    body: Any,
    *,
    handoff_id: Any,
    sol_identity: Any,
) -> dict[str, Any]:
    """驗證外部產生的 body 與已知 Evidence，建立 development-only provisional candidate。"""
    identity = context["identity"]
    input_binding = context["input_binding"]
    element_ids = [element["element_id"] for element in context["elements"]]
    known_evidence = {
        reference["evidence_id"]: reference for reference in context["evidence"]
    }

    if not _nonempty_text(handoff_id):
        return _candidate_failure("CONCEPT_LINEAGE_INVALID")
    if (
        not isinstance(sol_identity, dict)
        or set(sol_identity) != {"role", "model"}
        or not _nonempty_text(sol_identity["role"])
        or not _nonempty_text(sol_identity["model"])
    ):
        return _candidate_failure("CONCEPT_LINEAGE_INVALID")
    if not isinstance(body, dict) or set(body) != {
        "name",
        "definition",
        "scope",
        "evidence_ids",
    }:
        return _candidate_failure("CONCEPT_BODY_INVALID")
    if any(not _nonempty_text(body[field]) for field in ("name", "definition", "scope")):
        return _candidate_failure("CONCEPT_BODY_INVALID")
    evidence_ids = body["evidence_ids"]
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or any(not _nonempty_text(evidence_id) for evidence_id in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
    ):
        return _candidate_failure("CONCEPT_BODY_INVALID")
    anchor_evidence_id = context["evidence"][0]["evidence_id"]
    if (
        anchor_evidence_id not in evidence_ids
        or any(evidence_id not in known_evidence for evidence_id in evidence_ids)
    ):
        return _candidate_failure("CONCEPT_EVIDENCE_INVALID")

    candidate = {
        "schema": CONCEPT_CANDIDATE_SCHEMA,
        "development_only": True,
        "handoff_id": handoff_id,
        "identity": deepcopy(identity),
        "sol_identity": deepcopy(sol_identity),
        "prompt_identity": {
            "version": CONCEPT_PROMPT_VERSION,
            "sha256": CONCEPT_PROMPT_SHA256,
        },
        "context_binding": {
            "context_sha256": _canonical_sha256(context),
            **deepcopy(input_binding),
            "anchor_element_id": context["anchor_element_id"],
            "element_ids": element_ids,
        },
        "name": body["name"],
        "definition": body["definition"],
        "scope": body["scope"],
        "evidence": [deepcopy(known_evidence[evidence_id]) for evidence_id in evidence_ids],
    }
    candidate_sha256 = _canonical_sha256(candidate)
    return {
        "candidate_id": f"concept-candidate:sha256:{candidate_sha256}",
        **candidate,
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_code": "CONCEPT_CANDIDATE_NEEDS_REVIEW",
    }


def _valid_provisional_candidate(candidate: Any) -> bool:
    """驗證 provisional outcome 與不可變內容的 canonical stable ID。"""
    fields = {
        "schema",
        "candidate_id",
        "development_only",
        "handoff_id",
        "identity",
        "sol_identity",
        "prompt_identity",
        "context_binding",
        "name",
        "definition",
        "scope",
        "evidence",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if not isinstance(candidate, dict) or set(candidate) != fields:
        return False
    if (
        candidate["processing"] != "succeeded"
        or candidate["quality"] != "needs_review"
        or candidate["decision"] != "review"
        or candidate["reason_code"] != "CONCEPT_CANDIDATE_NEEDS_REVIEW"
    ):
        return False
    expected_candidate_sha256 = _canonical_sha256(_candidate_core(candidate))
    return expected_candidate_sha256 is not None and candidate["candidate_id"] == (
        f"concept-candidate:sha256:{expected_candidate_sha256}"
    )


def adjudicate_concept_candidate(
    candidate: Any, decision: Any
) -> dict[str, Any] | None:
    """以 retain 或 reject 產生不改寫語意、Evidence 與 lineage 的裁決 snapshot。"""
    if not _valid_provisional_candidate(candidate) or decision not in {
        "retain",
        "reject",
    }:
        return None
    snapshot = deepcopy(candidate)
    if decision == "retain":
        snapshot["quality"] = "accepted"
        snapshot["decision"] = "retain"
        snapshot["reason_code"] = "CONCEPT_CANDIDATE_ACCEPTED"
    else:
        snapshot["quality"] = "unsupported"
        snapshot["decision"] = "reject"
        snapshot["reason_code"] = "CONCEPT_CANDIDATE_REJECTED"
    return snapshot

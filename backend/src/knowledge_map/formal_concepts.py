from __future__ import annotations

from copy import deepcopy
from itertools import combinations
import unicodedata
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256


DEDUPLICATION_REQUEST_SCHEMA = "concept-deduplication-input/v1"
DEDUPLICATION_OUTPUT_SCHEMA = "concept-deduplication/v1"
RESOLUTION_OUTPUT_SCHEMA = "formal-concept-resolution/v2"
RETRIEVAL_POLICY = "grounded-concept-pair-retrieval/v2"
PAIR_DECISIONS = {"SAME", "DISTINCT", "UNCERTAIN"}
VERIFICATION_DIAGNOSTIC_FIELDS = {
    "qwen_same_pairs",
    "qwen_distinct_pairs",
    "qwen_uncertain_pairs",
    "verifier_requested_pairs",
    "verifier_scored_pairs",
    "verifier_allowed_pairs",
    "verifier_vetoed_pairs",
    "verifier_unsupported_pairs",
    "verifier_failed_pairs",
}
MAX_CANDIDATE_PAIRS = 16
MAX_EVIDENCE_ITEMS = 1
MAX_CONTEXT_HEADINGS = 2
MAX_COMPARISON_TEXT = 160


class FormalConceptError(ValueError):
    """Formal Concept 驗證失敗時只回傳固定原因。"""


def normalized_label(label: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", label).split()).casefold()


def _clean_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _search_terms(text: str) -> set[str]:
    """以 Unicode 字母數字詞建立不綁定語言的候選訊號。"""

    words: list[str] = []
    current = ""
    for character in normalized_label(text):
        if character.isalnum():
            current += character
        elif current:
            words.append(current)
            current = ""
    if current:
        words.append(current)
    return {word for word in words if len(word) > 1}


def _source_claims(concept: dict[str, Any]) -> list[dict[str, Any]]:
    return [concept["definition"], *concept["key_points"]]


def _context_headings(context: dict[str, Any]) -> list[str]:
    headings = {
        _clean_text(block["text"])
        for block in context["context_blocks"]
        if block.get("role") == "heading_ancestry"
        and isinstance(block.get("text"), str)
        and block["text"].strip()
    }
    return sorted(headings)[:MAX_CONTEXT_HEADINGS]


def _profile(
    concept: dict[str, Any],
    context: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    evidence_text_by_id: dict[str, str],
) -> dict[str, Any]:
    claims = _source_claims(concept)
    evidence_ids = sorted(
        {evidence_id for claim in claims for evidence_id in claim["evidence_ids"]}
    )
    headings = _context_headings(context)
    claim_text = " ".join(claim["text"] for claim in claims)
    evidence_text = " ".join(
        evidence_text_by_id[evidence_id] for evidence_id in evidence_ids
    )
    return {
        "concept": concept,
        "context": context,
        "label_key": normalized_label(concept["label"]),
        "label_terms": _search_terms(concept["label"]),
        "claim_terms": _search_terms(claim_text),
        "evidence_terms": _search_terms(evidence_text),
        "evidence_ids": set(evidence_ids),
        "section_ids": set(context["section_ids"]),
        "heading_terms": _search_terms(" ".join(headings)),
        "headings": headings,
        "evidence": [
            {
                "kind": evidence_by_id[evidence_id]["kind"],
                "text": evidence_text_by_id[evidence_id][:MAX_COMPARISON_TEXT],
            }
            for evidence_id in evidence_ids[:MAX_EVIDENCE_ITEMS]
        ],
    }


def _pair_signals(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[list[str], int]:
    signals = []
    if left["label_key"] == right["label_key"]:
        signals.append("exact_label")
    if left["label_terms"] & right["label_terms"]:
        signals.append("label_overlap")
    if left["claim_terms"] & right["claim_terms"]:
        signals.append("claim_overlap")
    if left["evidence_ids"] & right["evidence_ids"]:
        signals.append("shared_evidence")
    if left["evidence_terms"] & right["evidence_terms"]:
        signals.append("evidence_overlap")
    if left["section_ids"] & right["section_ids"]:
        signals.append("same_section")
    if left["heading_terms"] & right["heading_terms"]:
        signals.append("heading_context")
    page_distance = abs(
        left["context"]["page_number"] - right["context"]["page_number"]
    )
    if page_distance == 0:
        signals.append("same_page")
    elif page_distance == 1:
        signals.append("adjacent_page")
    return sorted(signals), page_distance


def _is_candidate(signals: list[str], page_distance: int) -> bool:
    signal_set = set(signals)
    return bool(
        "exact_label" in signal_set
        or "shared_evidence" in signal_set
        or "same_section" in signal_set
        or "heading_context" in signal_set
        or (
            {"label_overlap", "claim_overlap"} & signal_set
            and page_distance <= 4
        )
        or ("evidence_overlap" in signal_set and page_distance <= 2)
    )


def build_deduplication_request(
    study_material_output: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """用 grounded 現有訊號找比較對；訊號本身不會觸發合併。"""

    try:
        concepts = sorted(
            study_material_output["concepts"],
            key=lambda concept: concept["concept_id"],
        )
        contexts = {
            context["page_ref"]: context
            for context in study_material_output["document_contexts"]
        }
        evidence_by_id = {
            evidence["evidence_id"]: evidence
            for evidence in study_material_output["evidence_index"]
        }
        evidence_text_by_id = {
            evidence["evidence_id"]: evidence["text"]
            for evidence in study_material_output["evidence_text_index"]
        }
        if len(contexts) != len(study_material_output["document_contexts"]):
            raise KeyError
        profiles = [
            _profile(
                concept,
                contexts[concept["page_ref"]],
                evidence_by_id,
                evidence_text_by_id,
            )
            for concept in concepts
        ]
    except (KeyError, TypeError):
        raise FormalConceptError("RESOLUTION_SOURCE_INVALID") from None

    concept_aliases = {
        f"c{index}": concept["concept_id"]
        for index, concept in enumerate(concepts, start=1)
    }
    aliases_by_id = {
        concept_id: alias for alias, concept_id in concept_aliases.items()
    }
    ranked_pairs = []
    possible_pairs = len(concepts) * (len(concepts) - 1) // 2
    for left_index, left in enumerate(profiles):
        for right in profiles[left_index + 1 :]:
            signals, page_distance = _pair_signals(left, right)
            if not _is_candidate(signals, page_distance):
                continue
            left_id = left["concept"]["concept_id"]
            right_id = right["concept"]["concept_id"]
            priority = (
                -int("exact_label" in signals),
                -int("shared_evidence" in signals),
                -int("same_section" in signals or "heading_context" in signals),
                page_distance,
                left_id,
                right_id,
            )
            ranked_pairs.append((priority, left_id, right_id, signals))
    selected = sorted(ranked_pairs)[:MAX_CANDIDATE_PAIRS]
    selected_ids = {
        concept_id
        for _, left_id, right_id, _ in selected
        for concept_id in (left_id, right_id)
    }
    profiles_by_id = {
        profile["concept"]["concept_id"]: profile for profile in profiles
    }
    candidates = []
    for concept_id in sorted(selected_ids):
        profile = profiles_by_id[concept_id]
        concept = profile["concept"]
        context = profile["context"]
        candidates.append(
            {
                "id": aliases_by_id[concept_id],
                "label": concept["label"],
                "claims": [
                    claim["text"][:MAX_COMPARISON_TEXT]
                    for claim in _source_claims(concept)
                ],
                "evidence": deepcopy(profile["evidence"]),
                "document_context": {
                    "page_number": context["page_number"],
                    "section_ids": sorted(context["section_ids"]),
                    "headings": deepcopy(profile["headings"]),
                },
            }
        )
    pairs = [
        {
            "id": f"p{index}",
            "left": aliases_by_id[left_id],
            "right": aliases_by_id[right_id],
            "retrieval_signals": signals,
        }
        for index, (_, left_id, right_id, signals) in enumerate(selected, start=1)
    ]
    request = {
        "schema": DEDUPLICATION_REQUEST_SCHEMA,
        "retrieval": {
            "policy": RETRIEVAL_POLICY,
            "possible_pairs": possible_pairs,
            "candidate_pairs": len(ranked_pairs),
            "selected_pairs": len(pairs),
            "pair_ceiling": MAX_CANDIDATE_PAIRS,
        },
        "candidates": candidates,
        "pairs": pairs,
    }
    return request, concept_aliases


def validate_pair_decisions(
    candidate: Any, request: dict[str, Any]
) -> list[dict[str, str]]:
    """模型只能逐對回 SAME、DISTINCT 或 UNCERTAIN。"""

    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"schema", "pairs"}
        or candidate.get("schema") != DEDUPLICATION_OUTPUT_SCHEMA
        or not isinstance(candidate.get("pairs"), list)
    ):
        raise FormalConceptError("RESOLUTION_SCHEMA_INVALID")
    expected_ids = {pair["id"] for pair in request["pairs"]}
    decisions = []
    seen_ids = set()
    for pair in candidate["pairs"]:
        if (
            not isinstance(pair, dict)
            or set(pair) != {"id", "decision"}
            or pair.get("id") not in expected_ids
            or pair["id"] in seen_ids
            or pair.get("decision") not in PAIR_DECISIONS
        ):
            raise FormalConceptError("RESOLUTION_SCHEMA_INVALID")
        decisions.append({"id": pair["id"], "decision": pair["decision"]})
        seen_ids.add(pair["id"])
    if seen_ids != expected_ids:
        raise FormalConceptError("RESOLUTION_SOURCE_MISSING")
    return sorted(decisions, key=lambda pair: pair["id"])


def uncertain_pair_decisions(request: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"id": pair["id"], "decision": "UNCERTAIN"}
        for pair in request["pairs"]
    ]


def _verifier_text(
    label: str,
    claims: list[dict[str, Any]],
    evidence: list[dict[str, str]],
    headings: list[str],
) -> str:
    """只以教材語意欄位建立 verifier 文字，不包含 locator 或 retrieval 訊號。"""

    lines = ["Concept label:", label, "Claims:"]
    lines.extend(f"- {claim['text']}" for claim in claims)
    lines.append("Evidence:")
    lines.extend(
        f"- [{item['kind']}] {item['text']}" for item in evidence
    )
    lines.append("Semantic headings:")
    lines.extend(f"- {heading}" for heading in headings)
    if not headings:
        lines.append("- none")
    return "\n".join(lines)


def build_verifier_texts(
    study_material_output: dict[str, Any], concept_aliases: dict[str, str]
) -> dict[str, str]:
    """為 selected Concept 建立 exact、source-bound 雙向驗證文字。"""

    try:
        concepts_by_id = {
            concept["concept_id"]: concept
            for concept in study_material_output["concepts"]
        }
        contexts_by_page = {
            context["page_ref"]: context
            for context in study_material_output["document_contexts"]
        }
        evidence_by_id = {
            evidence["evidence_id"]: evidence
            for evidence in study_material_output["evidence_index"]
        }
        evidence_texts = {
            evidence["evidence_id"]: evidence["text"]
            for evidence in study_material_output["evidence_text_index"]
        }
        texts = {}
        for alias, concept_id in concept_aliases.items():
            concept = concepts_by_id[concept_id]
            claims = _source_claims(concept)
            evidence_ids = sorted({
                evidence_id
                for claim in claims
                for evidence_id in claim["evidence_ids"]
            })
            context = contexts_by_page[concept["page_ref"]]
            headings = sorted({
                _clean_text(block["text"])
                for block in context["context_blocks"]
                if block.get("role") == "heading_ancestry"
                and isinstance(block.get("text"), str)
                and block["text"].strip()
            })
            texts[alias] = _verifier_text(
                _clean_text(concept["label"]),
                claims,
                [
                    {
                        "kind": evidence_by_id[evidence_id]["kind"],
                        "text": evidence_texts[evidence_id],
                    }
                    for evidence_id in evidence_ids
                ],
                headings,
            )
        return texts
    except (KeyError, TypeError):
        raise FormalConceptError("RESOLUTION_SOURCE_INVALID") from None


def _safe_same_groups(
    source_ids: list[str],
    request: dict[str, Any],
    concept_aliases: dict[str, str],
    decisions: list[dict[str, str]],
) -> list[list[str]]:
    parent = {source_id: source_id for source_id in source_ids}

    def root(source_id: str) -> str:
        while parent[source_id] != source_id:
            parent[source_id] = parent[parent[source_id]]
            source_id = parent[source_id]
        return source_id

    decisions_by_id = {pair["id"]: pair["decision"] for pair in decisions}
    decision_by_sources = {}
    for pair in request["pairs"]:
        left = concept_aliases[pair["left"]]
        right = concept_aliases[pair["right"]]
        source_pair = frozenset((left, right))
        decision = decisions_by_id[pair["id"]]
        decision_by_sources[source_pair] = decision
        if decision == "SAME":
            left_root = root(left)
            right_root = root(right)
            if left_root != right_root:
                parent[right_root] = left_root

    proposed: dict[str, list[str]] = {}
    for source_id in source_ids:
        proposed.setdefault(root(source_id), []).append(source_id)
    groups = []
    for members in proposed.values():
        is_confirmed_clique = all(
            decision_by_sources.get(frozenset(pair)) == "SAME"
            for pair in combinations(members, 2)
        )
        if len(members) == 1 or is_confirmed_clique:
            groups.append(sorted(members))
        else:
            groups.extend([[source_id] for source_id in sorted(members)])
    return sorted(groups, key=lambda members: members[0])


def canonicalize_concepts(
    study_material_output: dict[str, Any],
    request: dict[str, Any],
    concept_aliases: dict[str, str],
    decisions: list[dict[str, str]],
    *,
    verification_diagnostics: dict[str, int] | None = None,
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """只有完整 SAME clique 會合併；其餘來源各自保留。"""

    try:
        source_by_id = {
            concept["concept_id"]: concept
            for concept in study_material_output["concepts"]
        }
        contexts_by_page = {
            context["page_ref"]: context
            for context in study_material_output["document_contexts"]
        }
        source_ids = sorted(source_by_id)
        if set(concept_aliases.values()) != set(source_ids):
            raise KeyError
        validated_decisions = validate_pair_decisions(
            {"schema": DEDUPLICATION_OUTPUT_SCHEMA, "pairs": decisions}, request
        )
        groups = _safe_same_groups(
            source_ids, request, concept_aliases, validated_decisions
        )
        if verification_diagnostics is None:
            verification_diagnostics = {
                field: 0 for field in VERIFICATION_DIAGNOSTIC_FIELDS
            }
        if (
            not isinstance(verification_diagnostics, dict)
            or set(verification_diagnostics) != VERIFICATION_DIAGNOSTIC_FIELDS
            or any(
                type(count) is not int or count < 0
                for count in verification_diagnostics.values()
            )
            or verification_diagnostics["qwen_same_pairs"]
            + verification_diagnostics["qwen_distinct_pairs"]
            + verification_diagnostics["qwen_uncertain_pairs"]
            != request["retrieval"]["selected_pairs"]
            or verification_diagnostics["verifier_requested_pairs"]
            != verification_diagnostics["qwen_same_pairs"]
            or verification_diagnostics["verifier_allowed_pairs"]
            != sum(
                pair["decision"] == "SAME" for pair in validated_decisions
            )
            or verification_diagnostics["verifier_requested_pairs"]
            != verification_diagnostics["verifier_scored_pairs"]
            + verification_diagnostics["verifier_unsupported_pairs"]
            + verification_diagnostics["verifier_failed_pairs"]
            or verification_diagnostics["verifier_scored_pairs"]
            != verification_diagnostics["verifier_allowed_pairs"]
            + verification_diagnostics["verifier_vetoed_pairs"]
        ):
            raise FormalConceptError("RESOLUTION_VERIFICATION_INVALID")
    except (FormalConceptError, KeyError, TypeError):
        raise FormalConceptError("RESOLUTION_SOURCE_INVALID") from None

    page_numbers = {
        page["page_ref"]: page["page_number"]
        for page in study_material_output["pages"]
    }
    formal_concepts = []
    for resolution_index, source_group in enumerate(groups):
        ordered_sources = sorted(
            (source_by_id[source_id] for source_id in source_group),
            key=lambda concept: (
                page_numbers[concept["page_ref"]], concept["concept_id"]
            ),
        )
        claims_by_id: dict[str, dict[str, Any]] = {}
        source_members = []
        for source in ordered_sources:
            source_claims = _source_claims(source)
            for claim in source_claims:
                existing = claims_by_id.get(claim["claim_id"])
                if existing is not None and existing != claim:
                    raise FormalConceptError("RESOLUTION_CLAIM_INVALID")
                claims_by_id.setdefault(claim["claim_id"], deepcopy(claim))
            context = contexts_by_page[source["page_ref"]]
            source_members.append(
                {
                    "source_concept_id": source["concept_id"],
                    "label": _clean_text(source["label"]),
                    "claim_ids": sorted(claim["claim_id"] for claim in source_claims),
                    "evidence_ids": sorted(
                        {
                            evidence_id
                            for claim in source_claims
                            for evidence_id in claim["evidence_ids"]
                        }
                    ),
                    "page_ref": source["page_ref"],
                    "document_context_id": context["context_id"],
                    "section_ids": sorted(context["section_ids"]),
                }
            )
        claims = list(claims_by_id.values())
        label = _clean_text(ordered_sources[0]["label"])
        aliases = sorted(
            {
                _clean_text(source["label"])
                for source in ordered_sources
                if _clean_text(source["label"]) != label
            }
        )
        operation = "MERGE" if len(source_group) > 1 else "KEEP"
        identity = {
            "group_id": "g1",
            "operation": operation,
            "source_concept_ids": source_group,
            "label": label,
            "aliases": aliases,
            "claims": claims,
            "source_members": source_members,
        }
        formal_concepts.append(
            {
                "formal_concept_id": (
                    "formal-concept:sha256:" + canonical_sha256(identity)
                ),
                **identity,
                "source_page_refs": sorted(
                    {source["page_ref"] for source in ordered_sources}
                ),
                "quality": "needs_review",
                "decision": "review",
                "reason_codes": ["FORMAL_CONCEPT_REVIEW_REQUIRED"],
                "resolution_order": [resolution_index, 0],
            }
        )
    covered_sources = [
        source_id
        for concept in formal_concepts
        for source_id in concept["source_concept_ids"]
    ]
    if sorted(covered_sources) != sorted(source_ids) or len(covered_sources) != len(
        set(covered_sources)
    ):
        raise FormalConceptError("RESOLUTION_SOURCE_MISSING")
    reason_codes = ["FORMAL_CONCEPT_REVIEW_REQUIRED"]
    if failure_reason is not None:
        reason_codes.append(failure_reason)
    return {
        "schema": RESOLUTION_OUTPUT_SCHEMA,
        "group_id": "g1",
        "input_binding": {
            "request_sha256": canonical_sha256(request),
            "request_schema": DEDUPLICATION_REQUEST_SCHEMA,
            "retrieval_policy": RETRIEVAL_POLICY,
        },
        "formal_concepts": formal_concepts,
        "pair_decisions": validated_decisions,
        "diagnostics": {
            **deepcopy(request["retrieval"]),
            **deepcopy(verification_diagnostics),
            "same_pairs": sum(
                pair["decision"] == "SAME" for pair in validated_decisions
            ),
            "distinct_pairs": sum(
                pair["decision"] == "DISTINCT" for pair in validated_decisions
            ),
            "uncertain_pairs": sum(
                pair["decision"] == "UNCERTAIN" for pair in validated_decisions
            ),
            "source_concepts_before": len(source_ids),
            "canonical_concepts_after": len(formal_concepts),
            "duplicate_delta": len(source_ids) - len(formal_concepts),
            "coverage_before": len(source_ids),
            "coverage_after": len(covered_sources),
        },
        "processing": "partial" if failure_reason is not None else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": sorted(reason_codes),
    }

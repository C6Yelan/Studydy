from __future__ import annotations

from collections import Counter
import re
from typing import Any, Callable
import unicodedata

from pdf_evidence.ocr_page_evidence import canonical_sha256


RELATION_REQUEST_SCHEMA = "formal-relation-input/v2"
RELATION_OUTPUT_SCHEMA = "formal-relations/v3"
RELATION_TYPES = {"prerequisite", "contains", "related"}
SYMMETRIC_TYPES = {"related"}
MAX_RELATION_PAIRS = 128
PAIR_BATCH_SIZE = 16

RelationVerifier = Callable[[str, dict[str, Any], dict[str, Any]], bool]


class RelationError(ValueError):
    """Relation candidate 不安全時只回傳固定 reason code。"""


def _text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _claims(concept: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(_text(claim["text"]), claim) for claim in concept["claims"]]


def _label_pattern(label: str) -> str | None:
    normalized = _text(label)
    if len(normalized.replace(" ", "")) < 2:
        return None
    escaped = re.escape(normalized)
    prefix = (
        r"(?<![0-9a-z])"
        if normalized[0].isascii() and normalized[0].isalnum()
        else ""
    )
    suffix = (
        r"(?![0-9a-z])"
        if normalized[-1].isascii() and normalized[-1].isalnum()
        else ""
    )
    return prefix + escaped + suffix


def _is_safe_prerequisite_claim(
    claim_text: str, *, reject_non_learning_context: bool
) -> bool:
    """否定或明示為非學習 dependency 時，不提出 prerequisite。"""

    negation_patterns = (
        r"\b(?:is|are|does|do|did|was|were|has|have) not\b",
        r"\b(?:isn't|aren't|doesn't|don't|didn't|wasn't|weren't|hasn't|haven't)\b",
        r"\bno prerequisite relationship\b",
        r"(?:不是|並非|不需要|不依賴|沒有(?:任何)?(?:先備|前置|依賴|需要))",
    )
    if any(re.search(pattern, claim_text) for pattern in negation_patterns):
        return False

    if not reject_non_learning_context:
        return True

    non_learning_contexts = (
        r"\b(?:runtime|execution|implementation|installation|memory|time|space|resource|"
        r"parameter|argument|input|package|library|dependency|dependencies|performance|"
        r"accuracy|quality|homework|project)\b",
        r"(?:執行期|執行時間|實作|安裝|記憶體|時間|空間|資源|參數|輸入|套件|函式庫|"
        r"效能|準確度|品質|作業|專案)",
    )
    return not any(re.search(pattern, claim_text) for pattern in non_learning_contexts)


def _directed_claims(
    source: dict[str, Any],
    target: dict[str, Any],
    relation_type: str,
) -> list[tuple[str, dict[str, Any]]]:
    """只接受同一句中可辨識兩端與方向的 hierarchy/dependency 敘述。"""

    source_label = _label_pattern(source["label"])
    target_label = _label_pattern(target["label"])
    if source_label is None or target_label is None:
        return []
    if relation_type == "contains":
        patterns = (
            rf"{source_label}.{{0,80}}\b(?:contains?|includes?|comprises?)\b.{{0,80}}{target_label}",
            rf"{target_label}.{{0,80}}\b(?:is|are)\b.{{0,20}}\b(?:a |an )?(?:part|component|sub-?concept) of\b.{{0,80}}{source_label}",
            rf"{source_label}.{{0,80}}(?:包含|包括).{{0,80}}{target_label}",
            rf"{source_label}.{{0,80}}由.{{0,80}}{target_label}.{{0,30}}(?:組成|構成)",
            rf"{target_label}.{{0,80}}是.{{0,30}}{source_label}.{{0,20}}的(?:一部分|組成部分|子概念|子觀念)",
        )
    elif relation_type == "prerequisite":
        patterns = (
            rf"{source_label}.{{0,20}}\bis (?:a )?prerequisite (?:of|for)\b.{{0,40}}{target_label}",
            rf"{source_label}.{{0,20}}\bis required before\b.{{0,40}}{target_label}",
            rf"{target_label}.{{0,40}}\bassumes? knowledge of\b.{{0,40}}{source_label}",
            rf"\bunderstanding\b.{{0,30}}{source_label}.{{0,20}}\b(?:is )?(?:required|necessary) for\b.{{0,40}}{target_label}",
            rf"\b(?:understand|learn|master)\b.{{0,30}}{source_label}.{{0,20}}\bbefore\b.{{0,40}}{target_label}",
            rf"\bbefore learning\b.{{0,30}}{target_label}.{{0,20}}\blearn\b.{{0,30}}{source_label}",
            rf"{target_label}.{{0,30}}\bbuilds? (?:on|upon)\b.{{0,30}}{source_label}",
            rf"{source_label}.{{0,20}}是.{{0,20}}{target_label}.{{0,10}}的(?:先備知識|先備概念)",
            rf"{source_label}.{{0,20}}是(?:學習|理解).{{0,10}}{target_label}.{{0,10}}的基礎",
            rf"(?:學習|理解).{{0,10}}{target_label}.{{0,10}}前(?:需要)?先(?:理解|學習|掌握).{{0,20}}{source_label}",
            rf"{target_label}.{{0,20}}需要先(?:理解|學習|掌握).{{0,20}}{source_label}",
            rf"{target_label}.{{0,20}}依賴(?:對)?.{{0,10}}{source_label}.{{0,10}}的理解",
            rf"先(?:理解|學習|掌握).{{0,20}}{source_label}.{{0,20}}再(?:理解|學習|掌握).{{0,20}}{target_label}",
            rf"掌握.{{0,20}}{source_label}.{{0,10}}後再學習.{{0,20}}{target_label}",
            rf"{target_label}.{{0,20}}建立在.{{0,20}}{source_label}.{{0,10}}的基礎上",
        )
        ambiguous_dependency_patterns = (
            rf"{target_label}.{{0,40}}\b(?:requires?|depends? on)\b.{{0,40}}{source_label}",
        )
    else:
        raise RelationError("RELATION_TYPE_INVALID")
    matching_claims = []
    for concept in (source, target):
        for claim_text, claim in _claims(concept):
            is_match = any(re.search(pattern, claim_text) for pattern in patterns)
            if relation_type == "prerequisite":
                safe_match = (
                    is_match
                    and _is_safe_prerequisite_claim(
                        claim_text, reject_non_learning_context=False
                    )
                )
                ambiguous_match = (
                    any(
                        re.search(pattern, claim_text)
                        for pattern in ambiguous_dependency_patterns
                    )
                    and _is_safe_prerequisite_claim(
                        claim_text, reject_non_learning_context=True
                    )
                )
                is_match = safe_match or ambiguous_match
            if is_match:
                matching_claims.append((concept["formal_concept_id"], claim))
    return matching_claims


def _formulas(concept: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for claim_text, claim in _claims(concept):
        expressions = re.findall(
            r"\$[^$\n]{2,120}\$|\\\([^\n]{2,120}?\\\)|\\\[[^\n]{2,120}?\\\]",
            claim_text,
        )
        for expression in expressions:
            found.setdefault(expression, []).append(claim)
    return found


def _related_claims(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[list[tuple[str, dict[str, Any]]], set[str]]:
    supporting: list[tuple[str, dict[str, Any]]] = []
    signals: set[str] = set()
    for concept, other in ((left, right), (right, left)):
        other_label = _label_pattern(other["label"])
        if other_label is None:
            continue
        for claim_text, claim in _claims(concept):
            cross_reference_patterns = (
                rf"\b(?:see|refer to|compare with|related to|example of|application of)\b.{{0,60}}{other_label}",
                rf"{other_label}.{{0,60}}\b(?:example|application|related topic)\b",
                rf"(?:參見|另見|比較|相關於|例如|應用於).{{0,40}}{other_label}",
                rf"{other_label}.{{0,40}}(?:的例子|的應用|相關主題)",
            )
            if any(re.search(pattern, claim_text) for pattern in cross_reference_patterns):
                supporting.append((concept["formal_concept_id"], claim))
                signals.add("cross_reference")
                continue
            if re.search(other_label, claim_text):
                supporting.append((concept["formal_concept_id"], claim))
                signals.add("label_mention")
    left_evidence = {
        evidence_id for _, claim in _claims(left) for evidence_id in claim["evidence_ids"]
    }
    right_evidence = {
        evidence_id for _, claim in _claims(right) for evidence_id in claim["evidence_ids"]
    }
    shared_evidence = left_evidence & right_evidence
    if shared_evidence:
        signals.add("shared_evidence")
        for concept in (left, right):
            for _, claim in _claims(concept):
                if set(claim["evidence_ids"]) & shared_evidence:
                    supporting.append((concept["formal_concept_id"], claim))
    left_formulas = _formulas(left)
    right_formulas = _formulas(right)
    shared_formulas = set(left_formulas) & set(right_formulas)
    if shared_formulas:
        signals.add("shared_formula")
        for expression in shared_formulas:
            supporting.extend(
                (left["formal_concept_id"], claim)
                for claim in left_formulas[expression]
            )
            supporting.extend(
                (right["formal_concept_id"], claim)
                for claim in right_formulas[expression]
            )
    unique = {
        (owner, claim["claim_id"]): (owner, claim) for owner, claim in supporting
    }
    return list(unique.values()), signals


def _relation_evidence(
    supporting: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """保留真正陳述 pair relation 的 claim owner，不假造 endpoint ownership。"""

    evidence = {
        (owner, claim["claim_id"]): {
            "owner_formal_concept_id": owner,
            "claim_id": claim["claim_id"],
            "evidence_ids": sorted(claim["evidence_ids"]),
        }
        for owner, claim in supporting
    }
    return [evidence[key] for key in sorted(evidence)]


def _pair_evidence(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    proposals = []
    conflicts: set[str] = set()
    signals: set[str] = set()
    for relation_type in ("contains", "prerequisite"):
        forward = _directed_claims(left, right, relation_type)
        reverse = _directed_claims(right, left, relation_type)
        if forward and reverse:
            conflicts.add(relation_type)
            signals.add("explicit_relation")
        elif forward or reverse:
            source, target, supporting = (
                (left, right, forward) if forward else (right, left, reverse)
            )
            relation_evidence = _relation_evidence(supporting)
            if relation_evidence:
                proposals.append(
                    {
                        "type": relation_type,
                        "source": source,
                        "target": target,
                        "relation_evidence": relation_evidence,
                    }
                )
            signals.add("explicit_relation")
    related_support, related_signals = _related_claims(left, right)
    signals.update(related_signals)
    if related_support and not proposals and not conflicts:
        source, target = sorted(
            (left, right), key=lambda concept: concept["formal_concept_id"]
        )
        if _relation_evidence(related_support):
            proposals.append(
                {
                    "type": "related",
                    "source": source,
                    "target": target,
                    "relation_evidence": _relation_evidence(related_support),
                }
            )
    return proposals, conflicts, signals


def has_structural_relation_evidence(
    pairs: list[tuple[str, str]], formal_concepts: list[dict[str, Any]]
) -> bool:
    formal_by_id = {concept["formal_concept_id"]: concept for concept in formal_concepts}
    return any(
        any(proposal["type"] in {"contains", "prerequisite"} for proposal in proposals)
        for left_id, right_id in pairs
        for proposals, _, _ in [
            _pair_evidence(formal_by_id[left_id], formal_by_id[right_id])
        ]
    )


def select_relation_pairs(
    formal_concepts: list[dict[str, Any]],
    page_numbers: dict[str, int],
    *,
    ceiling: int = MAX_RELATION_PAIRS,
) -> tuple[list[list[tuple[str, str]]], dict[str, Any]]:
    """先排 explicit cross-page signals，再補同頁/group/相鄰候選。"""

    ordered = sorted(
        formal_concepts,
        key=lambda concept: (
            min(page_numbers[page] for page in concept["source_page_refs"]),
            concept["resolution_order"],
            concept["formal_concept_id"],
        ),
    )
    candidates = []
    for index, left in enumerate(ordered):
        for right_index, right in enumerate(ordered[index + 1 :], start=index + 1):
            _, _, evidence_signals = _pair_evidence(left, right)
            signals = set(evidence_signals)
            if set(left["source_page_refs"]) & set(right["source_page_refs"]):
                signals.add("same_page")
            if left["group_id"] == right["group_id"]:
                signals.add("same_group")
            if right_index == index + 1:
                signals.add("adjacent")
            if signals:
                high_value = len(
                    signals
                    & {
                        "explicit_relation",
                        "cross_reference",
                        "label_mention",
                        "shared_evidence",
                        "shared_formula",
                    }
                )
                candidates.append(
                    (
                        0 if "explicit_relation" in signals else 1,
                        -high_value,
                        index,
                        right_index,
                        (left["formal_concept_id"], right["formal_concept_id"]),
                        signals,
                    )
                )
    candidates.sort(key=lambda candidate: candidate[:4])
    selected = candidates[:ceiling]
    signal_counts = Counter(signal for *_, signals in selected for signal in signals)
    pairs = [candidate[4] for candidate in selected]
    is_partial = len(candidates) > ceiling
    batches = [
        pairs[index : index + PAIR_BATCH_SIZE]
        for index in range(0, len(pairs), PAIR_BATCH_SIZE)
    ]
    return batches, {
        "processing": "partial" if is_partial else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": [
            "RELATION_PAIR_CEILING_EXCEEDED"
            if is_partial
            else "RELATION_REVIEW_REQUIRED"
        ],
        "diagnostics": {
            "possible_pairs": len(ordered) * (len(ordered) - 1) // 2,
            "candidate_pairs": len(candidates),
            "selected_pairs": len(selected),
            "selected_signal_counts": dict(sorted(signal_counts.items())),
        },
    }


def build_relation_request(
    pairs: list[tuple[str, str]],
    formal_concepts: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str], dict[str, tuple[str, str]]]:
    """建立只含目前 pair 所需的短 alias 與 grounded claims。"""

    concepts_by_id = {concept["formal_concept_id"]: concept for concept in formal_concepts}
    concept_aliases: dict[str, str] = {}
    evidence_aliases: dict[str, tuple[str, str]] = {}
    nodes = []
    for concept_id in dict.fromkeys(item for pair in pairs for item in pair):
        concept = concepts_by_id.get(concept_id)
        if concept is None:
            raise RelationError("RELATION_ENDPOINT_INVALID")
        concept_alias = f"n{len(concept_aliases) + 1}"
        concept_aliases[concept_alias] = concept_id
        evidence = []
        seen_evidence: set[str] = set()
        for claim in concept["claims"]:
            for evidence_id in claim["evidence_ids"]:
                if evidence_id in seen_evidence:
                    continue
                seen_evidence.add(evidence_id)
                alias = f"{concept_alias}e{len(evidence) + 1}"
                evidence_aliases[alias] = (concept_id, evidence_id)
                evidence.append({"id": alias, "claim_text": claim["text"]})
        nodes.append({"id": concept_alias, "label": concept["label"], "evidence": evidence})
    alias_by_id = {concept_id: alias for alias, concept_id in concept_aliases.items()}
    request = {
        "schema": RELATION_REQUEST_SCHEMA,
        "nodes": nodes,
        "pairs": [
            {"id": f"p{index}", "left": alias_by_id[left], "right": alias_by_id[right]}
            for index, (left, right) in enumerate(pairs, start=1)
        ],
    }
    return request, concept_aliases, evidence_aliases


def relation_premise(source: dict[str, Any], target: dict[str, Any]) -> str:
    """固定 A/B 方向；NLI 只驗證，不能交換 Evidence Gate 的方向。"""

    source_claims = "\n".join(claim["text"] for claim in source["claims"])
    target_claims = "\n".join(claim["text"] for claim in target["claims"])
    return (
        f"A: {source['label']}\nA grounded claims:\n{source_claims}\n"
        f"B: {target['label']}\nB grounded claims:\n{target_claims}"
    )


def build_relation_artifact(
    pairs: list[tuple[str, str]],
    formal_concepts: list[dict[str, Any]],
    evidence_pages: dict[str, str],
    verifier: RelationVerifier | None,
    *,
    verifier_failure_reason: str = "RELATION_VERIFIER_UNAVAILABLE",
) -> dict[str, Any]:
    """Evidence Gate 先成立；structural proposal 才可呼叫 verifier。"""

    request, concept_aliases, evidence_aliases = build_relation_request(
        pairs, formal_concepts
    )
    formal_by_id = {concept["formal_concept_id"]: concept for concept in formal_concepts}
    alias_by_id = {concept_id: alias for alias, concept_id in concept_aliases.items()}
    evidence_alias_by_id = {
        (owner_id, evidence_id): alias
        for alias, (owner_id, evidence_id) in evidence_aliases.items()
    }
    diagnostics = Counter()
    answers = []
    for expected in request["pairs"]:
        left = formal_by_id[concept_aliases[expected["left"]]]
        right = formal_by_id[concept_aliases[expected["right"]]]
        proposals, conflicts, _ = _pair_evidence(left, right)
        if conflicts:
            diagnostics["direction_conflicts"] += 1
            answers.append(
                {"id": expected["id"], "outcome": "uncertain", "relations": []}
            )
            continue
        accepted = []
        has_unsupported = False
        if proposals:
            diagnostics["evidence_gated_pairs"] += 1
        else:
            diagnostics["rejected_no_evidence"] += 1
        for proposal in proposals:
            diagnostics[f"{proposal['type']}_proposals"] += 1
            if proposal["type"] in {"contains", "prerequisite"}:
                diagnostics["structural_proposals"] += 1
            if proposal["type"] != "related":
                if verifier is None:
                    diagnostics["verifier_unsupported"] += 1
                    has_unsupported = True
                    continue
                diagnostics["verifier_calls"] += 1
                if not verifier(proposal["type"], proposal["source"], proposal["target"]):
                    diagnostics["verifier_rejected"] += 1
                    continue
                diagnostics["verifier_accepted"] += 1
            source_id = proposal["source"]["formal_concept_id"]
            target_id = proposal["target"]["formal_concept_id"]
            accepted.append(
                {
                    "type": proposal["type"],
                    "source": alias_by_id[source_id],
                    "target": alias_by_id[target_id],
                    "relation_evidence_ids": sorted({
                        evidence_alias_by_id[
                            (evidence["owner_formal_concept_id"], evidence_id)
                        ]
                        for evidence in proposal["relation_evidence"]
                        for evidence_id in evidence["evidence_ids"]
                    }),
                }
            )
        diagnostics["accepted_relations"] += len(accepted)
        answers.append(
            {
                "id": expected["id"],
                "outcome": (
                    "relations"
                    if accepted
                    else "uncertain"
                    if has_unsupported
                    else "no_relation"
                ),
                "relations": accepted,
            }
        )
    artifact = validate_relations(
        {"schema": RELATION_OUTPUT_SCHEMA, "pairs": answers},
        request=request,
        concept_aliases=concept_aliases,
        evidence_aliases=evidence_aliases,
        formal_concepts=formal_concepts,
        evidence_pages=evidence_pages,
    )
    artifact["diagnostics"] = dict(sorted(diagnostics.items()))
    if diagnostics["verifier_unsupported"]:
        artifact.update(
            {
                "processing": "partial",
                "reason_codes": [verifier_failure_reason],
            }
        )
    return artifact


def validate_relations(
    candidate: Any,
    *,
    request: dict[str, Any],
    concept_aliases: dict[str, str],
    evidence_aliases: dict[str, tuple[str, str]],
    formal_concepts: list[dict[str, Any]],
    evidence_pages: dict[str, str],
) -> dict[str, Any]:
    """驗證 endpoint、雙側 Evidence ownership、三類方向與重覆衝突。"""

    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"schema", "pairs"}
        or candidate.get("schema") != RELATION_OUTPUT_SCHEMA
        or not isinstance(candidate.get("pairs"), list)
    ):
        raise RelationError("RELATION_SCHEMA_INVALID")
    expected = {pair["id"]: pair for pair in request["pairs"]}
    if len(candidate["pairs"]) != len(expected):
        raise RelationError("RELATION_PAIR_MISSING")
    formal_by_id = {concept["formal_concept_id"]: concept for concept in formal_concepts}
    seen_pairs: set[str] = set()
    relations = []
    relation_keys: set[tuple[str, str, str]] = set()
    directed_pairs: dict[tuple[str, str], str] = {}
    for answer in candidate["pairs"]:
        if (
            not isinstance(answer, dict)
            or set(answer) != {"id", "outcome", "relations"}
            or answer["id"] not in expected
            or answer["id"] in seen_pairs
            or answer["outcome"] not in {"relations", "no_relation", "uncertain"}
            or not isinstance(answer["relations"], list)
            or (answer["outcome"] != "relations" and answer["relations"])
            or (answer["outcome"] == "relations" and not answer["relations"])
        ):
            raise RelationError("RELATION_SCHEMA_INVALID")
        seen_pairs.add(answer["id"])
        allowed = {
            expected[answer["id"]]["left"],
            expected[answer["id"]]["right"],
        }
        for source_relation in answer["relations"]:
            if (
                not isinstance(source_relation, dict)
                or set(source_relation) != {
                    "type",
                    "source",
                    "target",
                    "relation_evidence_ids",
                }
                or source_relation["type"] not in RELATION_TYPES
                or {source_relation["source"], source_relation["target"]} != allowed
                or source_relation["source"] == source_relation["target"]
            ):
                raise RelationError("RELATION_ENDPOINT_INVALID")
            source_id = concept_aliases.get(source_relation["source"])
            target_id = concept_aliases.get(source_relation["target"])
            if source_id is None or target_id is None:
                raise RelationError("RELATION_ENDPOINT_INVALID")
            aliases = source_relation["relation_evidence_ids"]
            if (
                not isinstance(aliases, list)
                or not aliases
                or len(aliases) != len(set(aliases))
                or any(alias not in evidence_aliases for alias in aliases)
            ):
                raise RelationError("RELATION_EVIDENCE_INVALID")
            relation_type = source_relation["type"]
            expected_proposals, conflicts, _ = _pair_evidence(
                formal_by_id[concept_aliases[expected[answer["id"]]["left"]]],
                formal_by_id[concept_aliases[expected[answer["id"]]["right"]]],
            )
            matching_proposals = [
                proposal
                for proposal in expected_proposals
                if proposal["type"] == relation_type
                and proposal["source"]["formal_concept_id"] == source_id
                and proposal["target"]["formal_concept_id"] == target_id
            ]
            provided_evidence = {evidence_aliases[alias] for alias in aliases}
            if conflicts or len(matching_proposals) != 1:
                raise RelationError("RELATION_EVIDENCE_INVALID")
            proposal = matching_proposals[0]
            expected_evidence = {
                (item["owner_formal_concept_id"], evidence_id)
                for item in proposal["relation_evidence"]
                for evidence_id in item["evidence_ids"]
            }
            if provided_evidence != expected_evidence:
                raise RelationError("RELATION_EVIDENCE_INVALID")
            relation_evidence = proposal["relation_evidence"]
            for item in relation_evidence:
                owner = item["owner_formal_concept_id"]
                claims = {
                    claim["claim_id"]: claim for claim in formal_by_id[owner]["claims"]
                }
                claim = claims.get(item["claim_id"])
                if (
                    owner not in {source_id, target_id}
                    or claim is None
                    or item["evidence_ids"] != sorted(set(item["evidence_ids"]))
                    or not item["evidence_ids"]
                    or not set(item["evidence_ids"]) <= set(claim["evidence_ids"])
                    or any(
                        evidence_pages.get(evidence_id)
                        not in set(formal_by_id[owner]["source_page_refs"])
                        for evidence_id in item["evidence_ids"]
                    )
                ):
                    raise RelationError("RELATION_EVIDENCE_INVALID")
            if relation_type in SYMMETRIC_TYPES and target_id < source_id:
                source_id, target_id = target_id, source_id
            key = (relation_type, source_id, target_id)
            if key in relation_keys:
                raise RelationError("RELATION_DUPLICATE")
            opposite = (target_id, source_id)
            if relation_type not in SYMMETRIC_TYPES and opposite in directed_pairs:
                raise RelationError("RELATION_CONFLICT")
            relation_keys.add(key)
            directed_pairs[(source_id, target_id)] = relation_type
            identity = {
                "type": relation_type,
                "source_formal_concept_id": source_id,
                "target_formal_concept_id": target_id,
                "relation_evidence": relation_evidence,
            }
            relations.append(
                {
                    "relation_id": "formal-relation:sha256:" + canonical_sha256(identity),
                    **identity,
                    "quality": "needs_review",
                    "decision": "review",
                    "reason_codes": ["RELATION_REVIEW_REQUIRED"],
                }
            )
    if seen_pairs != set(expected):
        raise RelationError("RELATION_PAIR_MISSING")
    return {
        "schema": RELATION_OUTPUT_SCHEMA,
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
        "processing": "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": ["RELATION_REVIEW_REQUIRED"],
    }

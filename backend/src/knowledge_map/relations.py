from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re
from typing import Any, Callable
import unicodedata

from pdf_evidence.ocr_page_evidence import canonical_sha256


RELATION_REQUEST_SCHEMA = "formal-relation-input/v3"
RELATION_PROPOSAL_SCHEMA = "formal-relation-proposals/v1"
RELATION_OUTPUT_SCHEMA = "formal-relations/v4"
RELATION_TYPES = {"prerequisite", "contains", "related"}
MAX_RELATION_PAIRS = 128
PAIR_BATCH_SIZE = 16

RelationVerifier = Callable[
    [str, dict[str, Any], dict[str, Any]], tuple[bool | None, str | None]
]


class RelationError(ValueError):
    """Relation candidate 不安全時只回傳固定 reason code。"""


def _text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _label_pattern(label: str) -> str | None:
    normalized = _text(label)
    if len(normalized.replace(" ", "")) < 2:
        return None
    escaped = re.escape(normalized)
    prefix = r"(?<![0-9a-z])" if normalized[0].isascii() else ""
    suffix = r"(?![0-9a-z])" if normalized[-1].isascii() else ""
    return prefix + escaped + suffix


def _claims_mention(
    concept: dict[str, Any], other: dict[str, Any]
) -> bool:
    pattern = _label_pattern(other["label"])
    return pattern is not None and any(
        re.search(pattern, _text(claim["text"]))
        for claim in concept["claims"]
    )


def _explicit_relation_signal(
    left: dict[str, Any], right: dict[str, Any]
) -> bool:
    """Regex 只找值得交給模型的 pair，不決定 Relation。"""

    left_label = _label_pattern(left["label"])
    right_label = _label_pattern(right["label"])
    if left_label is None or right_label is None:
        return False
    relation_words = (
        r"contains?|includes?|comprises?|component|sub-?concept|"
        r"prerequisite|required before|assumes? knowledge|builds? on|"
        r"需要先|先備|基礎|依賴|包含|包括|組成|構成"
    )
    return any(
        re.search(left_label, text)
        and re.search(right_label, text)
        and re.search(relation_words, text)
        for concept in (left, right)
        for text in (_text(claim["text"]) for claim in concept["claims"])
    )


def _shared_formula(left: dict[str, Any], right: dict[str, Any]) -> bool:
    pattern = r"\$[^$\n]{2,120}\$|\\\([^\n]{2,120}?\\\)|\\\[[^\n]{2,120}?\\\]"
    expressions = []
    for concept in (left, right):
        expressions.append({
            expression
            for claim in concept["claims"]
            for expression in re.findall(pattern, claim["text"])
        })
    return bool(expressions[0] & expressions[1])


def _pair_signals(
    left: dict[str, Any], right: dict[str, Any]
) -> set[str]:
    if _text(left["label"]) == _text(right["label"]):
        return set()
    signals: set[str] = set()
    if _explicit_relation_signal(left, right):
        signals.add("explicit_relation")
    if _claims_mention(left, right) or _claims_mention(right, left):
        signals.add("label_mention")
    left_evidence = {
        evidence_id
        for claim in left["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    right_evidence = {
        evidence_id
        for claim in right["claims"]
        for evidence_id in claim["evidence_ids"]
    }
    if left_evidence & right_evidence:
        signals.add("shared_evidence")
    if _shared_formula(left, right):
        signals.add("shared_formula")
    left_members = left["source_members"]
    right_members = right["source_members"]
    if set(left["source_page_refs"]) & set(right["source_page_refs"]):
        signals.add("same_page")
    if {member["document_context_id"] for member in left_members} & {
        member["document_context_id"] for member in right_members
    }:
        signals.add("same_context")
    if {
        section_id
        for member in left_members
        for section_id in member["section_ids"]
    } & {
        section_id
        for member in right_members
        for section_id in member["section_ids"]
    }:
        signals.add("same_section")
    if left["group_id"] == right["group_id"]:
        signals.add("same_group")
    return signals


def select_relation_pairs(
    formal_concepts: list[dict[str, Any]],
    page_numbers: dict[str, int],
    *,
    ceiling: int = MAX_RELATION_PAIRS,
) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
    """先選 semantic/context lineage，再以 adjacent 與 group 補足候選。"""

    ordered = sorted(
        formal_concepts,
        key=lambda concept: (
            min(page_numbers[page] for page in concept["source_page_refs"]),
            concept["resolution_order"],
            concept["formal_concept_id"],
        ),
    )
    strong = {"explicit_relation", "label_mention", "shared_evidence", "shared_formula"}
    contextual = {"same_section", "same_context"}
    candidates = []
    for index, left in enumerate(ordered):
        for right_index, right in enumerate(ordered[index + 1 :], start=index + 1):
            signals = _pair_signals(left, right)
            if right_index == index + 1:
                signals.add("adjacent")
            if not signals:
                continue
            rank = (
                0 if signals & strong
                else 1 if signals & contextual
                else 2 if "same_page" in signals
                else 3 if "adjacent" in signals
                else 4
            )
            candidates.append((
                rank,
                -len(signals & (strong | contextual)),
                index,
                right_index,
                {
                    "left": left["formal_concept_id"],
                    "right": right["formal_concept_id"],
                    "signals": sorted(signals),
                },
            ))
    candidates.sort(key=lambda candidate: candidate[:4])
    selected = candidates[:ceiling]
    pairs = [candidate[4] for candidate in selected]
    signal_counts = Counter(
        signal for pair in pairs for signal in pair["signals"]
    )
    batches = [
        pairs[index : index + PAIR_BATCH_SIZE]
        for index in range(0, len(pairs), PAIR_BATCH_SIZE)
    ]
    is_partial = len(candidates) > ceiling
    return batches, {
        "processing": "partial" if is_partial else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": [
            "RELATION_PAIR_CEILING_EXCEEDED"
            if is_partial else "RELATION_REVIEW_REQUIRED"
        ],
        "diagnostics": {
            "possible_pairs": len(ordered) * (len(ordered) - 1) // 2,
            "candidate_pairs": len(candidates),
            "selected_pairs": len(selected),
            "selected_signal_counts": dict(sorted(signal_counts.items())),
        },
    }


def build_relation_request(
    pairs: list[dict[str, Any]],
    formal_concepts: list[dict[str, Any]],
    page_numbers: dict[str, int],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """以短 alias 傳送當前 Claim、Evidence 與 document context lineage。"""

    concepts_by_id = {
        concept["formal_concept_id"]: concept for concept in formal_concepts
    }
    bindings: dict[str, dict[str, Any]] = {
        "concepts": {}, "claims": {}, "evidence": {}, "contexts": {}
    }
    nodes = []
    for concept_id in dict.fromkeys(
        item for pair in pairs for item in (pair["left"], pair["right"])
    ):
        concept = concepts_by_id.get(concept_id)
        if concept is None:
            raise RelationError("RELATION_ENDPOINT_INVALID")
        node_id = f"n{len(nodes) + 1}"
        bindings["concepts"][node_id] = {"formal_concept_id": concept_id}
        evidence_aliases = {}
        claims = []
        for claim_index, claim in enumerate(concept["claims"], start=1):
            claim_alias = f"{node_id}c{claim_index}"
            bindings["claims"][claim_alias] = {
                "owner_formal_concept_id": concept_id,
                "claim_id": claim["claim_id"],
            }
            claim_evidence = []
            for evidence_id in claim["evidence_ids"]:
                evidence_alias = evidence_aliases.get(evidence_id)
                if evidence_alias is None:
                    evidence_alias = f"{node_id}e{len(evidence_aliases) + 1}"
                    evidence_aliases[evidence_id] = evidence_alias
                    bindings["evidence"][evidence_alias] = {
                        "owner_formal_concept_id": concept_id,
                        "evidence_id": evidence_id,
                    }
                claim_evidence.append(evidence_alias)
            claims.append({
                "id": claim_alias,
                "text": claim["text"],
                "evidence_ids": sorted(claim_evidence),
            })
        contexts = []
        for context_index, member in enumerate(
            sorted(
                concept["source_members"],
                key=lambda item: (
                    page_numbers[item["page_ref"]],
                    item["document_context_id"],
                ),
            ),
            start=1,
        ):
            context_alias = f"{node_id}x{context_index}"
            bindings["contexts"][context_alias] = {
                "owner_formal_concept_id": concept_id,
                "document_context_id": member["document_context_id"],
                "page_ref": member["page_ref"],
                "section_ids": member["section_ids"],
            }
            contexts.append({
                "id": context_alias,
                "page_number": page_numbers[member["page_ref"]],
                "section_ids": [
                    f"{context_alias}s{index}"
                    for index in range(1, len(member["section_ids"]) + 1)
                ],
            })
        nodes.append({
            "id": node_id,
            "label": concept["label"],
            "claims": claims,
            "contexts": contexts,
        })
    alias_by_id = {
        value["formal_concept_id"]: alias
        for alias, value in bindings["concepts"].items()
    }
    request = {
        "schema": RELATION_REQUEST_SCHEMA,
        "nodes": nodes,
        "pairs": [
            {
                "id": f"p{index}",
                "left": alias_by_id[pair["left"]],
                "right": alias_by_id[pair["right"]],
                "retrieval_signals": pair["signals"],
            }
            for index, pair in enumerate(pairs, start=1)
        ],
    }
    return request, bindings


def relation_premise(source: dict[str, Any], target: dict[str, Any]) -> str:
    source_claims = "\n".join(claim["text"] for claim in source["claims"])
    target_claims = "\n".join(claim["text"] for claim in target["claims"])
    return (
        f"A: {source['label']}\nA grounded claims:\n{source_claims}\n"
        f"B: {target['label']}\nB grounded claims:\n{target_claims}"
    )


def _valid_aliases(value: Any, allowed: set[str]) -> bool:
    return (
        isinstance(value, list)
        and len(value) == len(set(value))
        and all(isinstance(item, str) and item in allowed for item in value)
    )


def _creates_cycle(
    relations: list[dict[str, Any]], relation_type: str, source: str, target: str
) -> bool:
    adjacency: dict[str, set[str]] = {}
    for relation in relations:
        if relation["type"] == relation_type:
            adjacency.setdefault(
                relation["source_formal_concept_id"], set()
            ).add(relation["target_formal_concept_id"])
    pending = [target]
    seen = set()
    while pending:
        current = pending.pop()
        if current == source:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def failed_relation_artifact(
    request: dict[str, Any], reason_code: str
) -> dict[str, Any]:
    return {
        "schema": RELATION_OUTPUT_SCHEMA,
        "input_binding": {
            "request_schema": RELATION_REQUEST_SCHEMA,
            "request_sha256": canonical_sha256(request),
        },
        "pair_outcomes": [],
        "rejected_pairs": [
            {
                "pair_id": pair["id"],
                "processing": "failed",
                "quality": "needs_review",
                "decision": "reject",
                "reason_codes": [reason_code],
            }
            for pair in request["pairs"]
        ],
        "relations": [],
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": [reason_code],
        "diagnostics": {"invalid_pairs": len(request["pairs"])},
    }


def _rejection(pair_id: str, reason_code: str) -> dict[str, Any]:
    return {
        "pair_id": pair_id,
        "processing": "failed",
        "quality": "needs_review",
        "decision": "reject",
        "reason_codes": [reason_code],
    }


def validate_relation_proposals(
    candidate: Any,
    *,
    request: dict[str, Any],
    bindings: dict[str, dict[str, Any]],
    formal_concepts: list[dict[str, Any]],
    evidence_pages: dict[str, str],
    verifier: RelationVerifier | None,
    prior_relations: list[dict[str, Any]],
) -> dict[str, Any]:
    """逐 pair 驗證模型 aliases；同批安全 pair 可在局部錯誤下保留。"""

    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"schema", "pairs"}
        or candidate.get("schema") != RELATION_PROPOSAL_SCHEMA
        or not isinstance(candidate.get("pairs"), list)
    ):
        raise RelationError("RELATION_SCHEMA_INVALID")
    expected = {pair["id"]: pair for pair in request["pairs"]}
    supplied: dict[str, list[Any]] = {pair_id: [] for pair_id in expected}
    unexpected = 0
    for answer in candidate["pairs"]:
        pair_id = answer.get("id") if isinstance(answer, dict) else None
        if pair_id not in supplied:
            unexpected += 1
        else:
            supplied[pair_id].append(answer)
    concepts = {
        concept["formal_concept_id"]: concept for concept in formal_concepts
    }
    claim_bindings = bindings["claims"]
    evidence_bindings = bindings["evidence"]
    context_bindings = bindings["contexts"]
    pair_outcomes = []
    rejected_pairs = []
    relations = []
    accepted_global = [*prior_relations]
    diagnostics = Counter(model_calls=1, unexpected_pairs=unexpected)
    for pair_id, pair in expected.items():
        answers = supplied[pair_id]
        reason_code = None
        if len(answers) != 1:
            reason_code = "RELATION_PAIR_MISSING"
        else:
            answer = answers[0]
            basis = answer.get("inference_basis")
            if (
                set(answer)
                != {
                    "id", "outcome", "source", "target", "reason",
                    "inference_basis", "needs_review",
                }
                or answer.get("outcome")
                not in {"no_relation", *RELATION_TYPES}
                or type(answer.get("needs_review")) is not bool
                or not isinstance(answer.get("reason"), str)
                or not 4 <= len(answer["reason"]) <= 500
                or " ".join(
                    unicodedata.normalize("NFKC", answer["reason"]).split()
                ) != answer["reason"]
                or not isinstance(basis, dict)
                or set(basis)
                != {"kind", "claim_ids", "evidence_ids", "context_ids"}
                or basis.get("kind")
                not in {"claim_semantics", "document_structure", "combined"}
            ):
                reason_code = "RELATION_SCHEMA_INVALID"
        if reason_code is not None:
            diagnostics["invalid_pairs"] += 1
            rejected_pairs.append(_rejection(pair_id, reason_code))
            continue

        answer = answers[0]
        basis = answer["inference_basis"]
        allowed_nodes = {pair["left"], pair["right"]}
        if (
            {answer["source"], answer["target"]} != allowed_nodes
            or answer["source"] == answer["target"]
            or answer["outcome"] == "no_relation"
            and (
                answer["source"] != pair["left"]
                or answer["target"] != pair["right"]
            )
            or not _valid_aliases(basis["claim_ids"], set(claim_bindings))
            or not _valid_aliases(
                basis["evidence_ids"], set(evidence_bindings)
            )
            or not _valid_aliases(
                basis["context_ids"], set(context_bindings)
            )
        ):
            diagnostics["invalid_pairs"] += 1
            rejected_pairs.append(
                _rejection(pair_id, "RELATION_EVIDENCE_INVALID")
            )
            continue
        source_id = bindings["concepts"][answer["source"]]["formal_concept_id"]
        target_id = bindings["concepts"][answer["target"]]["formal_concept_id"]
        endpoint_ids = {source_id, target_id}
        if (
            any(
                claim_bindings[alias]["owner_formal_concept_id"]
                not in endpoint_ids
                for alias in basis["claim_ids"]
            )
            or any(
                evidence_bindings[alias]["owner_formal_concept_id"]
                not in endpoint_ids
                for alias in basis["evidence_ids"]
            )
            or any(
                context_bindings[alias]["owner_formal_concept_id"]
                not in endpoint_ids
                for alias in basis["context_ids"]
            )
            or answer["outcome"] != "no_relation"
            and (
                not basis["claim_ids"]
                or not basis["evidence_ids"]
                or basis["kind"] in {"document_structure", "combined"}
                and not basis["context_ids"]
            )
        ):
            diagnostics["invalid_pairs"] += 1
            rejected_pairs.append(
                _rejection(pair_id, "RELATION_EVIDENCE_INVALID")
            )
            continue

        relation_evidence = []
        selected_evidence = {
            evidence_bindings[alias]["evidence_id"]
            for alias in basis["evidence_ids"]
        }
        evidence_is_valid = True
        for claim_alias in basis["claim_ids"]:
            binding = claim_bindings[claim_alias]
            claim = next(
                item
                for item in concepts[binding["owner_formal_concept_id"]]["claims"]
                if item["claim_id"] == binding["claim_id"]
            )
            used = sorted(set(claim["evidence_ids"]) & selected_evidence)
            if not used or any(
                evidence_pages.get(evidence_id)
                not in concepts[binding["owner_formal_concept_id"]][
                    "source_page_refs"
                ]
                for evidence_id in used
            ):
                evidence_is_valid = False
                break
            relation_evidence.append({
                "owner_formal_concept_id": binding["owner_formal_concept_id"],
                "claim_id": binding["claim_id"],
                "evidence_ids": used,
            })
        used_evidence = {
            evidence_id
            for item in relation_evidence
            for evidence_id in item["evidence_ids"]
        }
        if used_evidence != selected_evidence:
            evidence_is_valid = False
        if not evidence_is_valid:
            diagnostics["invalid_pairs"] += 1
            rejected_pairs.append(
                _rejection(pair_id, "RELATION_EVIDENCE_INVALID")
            )
            continue

        diagnostics[f"model_{answer['outcome']}_pairs"] += 1
        diagnostics["model_review_pairs"] += answer["needs_review"]
        if answer["outcome"] == "related" and target_id < source_id:
            source_id, target_id = target_id, source_id
        pair_outcomes.append({
            "pair_id": pair_id,
            "outcome": answer["outcome"],
            "source_formal_concept_id": source_id,
            "target_formal_concept_id": target_id,
            "reason": answer["reason"],
            "needs_review": answer["needs_review"],
        })
        if answer["outcome"] == "no_relation":
            continue

        relation_type = answer["outcome"]
        needs_review = answer["needs_review"]
        if relation_type in {"contains", "prerequisite"}:
            if verifier is None:
                diagnostics["verifier_unsupported"] += 1
                needs_review = True
            else:
                diagnostics["verifier_calls"] += 1
                verdict, verifier_reason = verifier(
                    relation_type, concepts[source_id], concepts[target_id]
                )
                if verdict is None:
                    diagnostics["verifier_unsupported"] += 1
                    diagnostics["verifier_failures"] += verifier_reason is not None
                    needs_review = True
                elif verdict:
                    diagnostics["verifier_accepted"] += 1
                else:
                    diagnostics["verifier_rejected"] += 1
                    needs_review = True
        conflict = any(
            {
                relation["source_formal_concept_id"],
                relation["target_formal_concept_id"],
            }
            == {source_id, target_id}
            for relation in accepted_global
        )
        cycle = relation_type in {"contains", "prerequisite"} and _creates_cycle(
            accepted_global, relation_type, source_id, target_id
        )
        if conflict or cycle:
            pair_outcomes.pop()
            diagnostics["canonical_rejections"] += 1
            reason_code = (
                "RELATION_CONFLICT"
                if conflict
                else "PREREQUISITE_CYCLE"
                if relation_type == "prerequisite"
                else "CONTAINS_CYCLE"
            )
            rejected_pairs.append(_rejection(pair_id, reason_code))
            continue

        relation_context = [
            deepcopy(context_bindings[alias])
            for alias in sorted(basis["context_ids"])
        ]
        relation_evidence.sort(
            key=lambda item: (
                item["owner_formal_concept_id"], item["claim_id"]
            )
        )
        relation_context.sort(
            key=lambda item: (
                item["owner_formal_concept_id"], item["page_ref"]
            )
        )
        identity = {
            "type": relation_type,
            "source_formal_concept_id": source_id,
            "target_formal_concept_id": target_id,
            "reason": answer["reason"],
            "inference_basis": basis["kind"],
            "relation_evidence": relation_evidence,
            "relation_context": relation_context,
        }
        relation = {
            "relation_id": "formal-relation:sha256:" + canonical_sha256(identity),
            **identity,
            "needs_review": needs_review,
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": ["RELATION_REVIEW_REQUIRED"],
        }
        relations.append(relation)
        accepted_global.append(relation)
        diagnostics["accepted_relations"] += 1

    is_failed = bool(rejected_pairs) and not pair_outcomes
    is_partial = bool(rejected_pairs) and bool(pair_outcomes)
    reason_codes = {"RELATION_REVIEW_REQUIRED"}
    if rejected_pairs:
        rejection_reasons = {
            reason
            for rejected in rejected_pairs
            for reason in rejected["reason_codes"]
        }
        canonical_reasons = rejection_reasons & {
            "RELATION_CONFLICT", "PREREQUISITE_CYCLE", "CONTAINS_CYCLE"
        }
        reason_codes.update(canonical_reasons or {"MODEL_OUTPUT_INVALID"})
    return {
        "schema": RELATION_OUTPUT_SCHEMA,
        "input_binding": {
            "request_schema": RELATION_REQUEST_SCHEMA,
            "request_sha256": canonical_sha256(request),
        },
        "pair_outcomes": pair_outcomes,
        "rejected_pairs": rejected_pairs,
        "relations": sorted(relations, key=lambda item: item["relation_id"]),
        "processing": (
            "failed" if is_failed else "partial" if is_partial else "succeeded"
        ),
        "quality": "needs_review",
        "decision": "reject" if is_failed else "review",
        "reason_codes": sorted(reason_codes),
        "diagnostics": dict(sorted(diagnostics.items())),
    }

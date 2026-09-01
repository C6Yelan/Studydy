from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from pdf_evidence.ocr_page_evidence import canonical_sha256


Verifier = Callable[[dict[str, Any]], bool | None]


def _can_reach(
    outgoing: dict[str, set[str]], start: str, target: str
) -> bool:
    pending = [start]
    seen = set()
    while pending:
        concept_id = pending.pop()
        if concept_id == target:
            return True
        if concept_id in seen:
            continue
        seen.add(concept_id)
        pending.extend(outgoing.get(concept_id, ()))
    return False


def build_prerequisite_constraints(
    proposals: list[dict[str, Any]],
    formal_concepts: list[dict[str, Any]],
    verifier_binding: dict[str, str],
    verifier: Verifier,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """只把明確正向、grounded 且不衝突的提案轉成 Path constraint。"""

    if (
        not isinstance(proposals, list)
        or not isinstance(formal_concepts, list)
        or not isinstance(verifier_binding, dict)
        or set(verifier_binding) != {"model_id", "revision", "policy"}
        or any(not isinstance(value, str) or not value for value in verifier_binding.values())
        or not callable(verifier)
    ):
        raise ValueError("PREREQUISITE_INPUT_INVALID")
    concepts = {
        concept.get("formal_concept_id"): concept
        for concept in formal_concepts
        if isinstance(concept, dict)
    }
    if len(concepts) != len(formal_concepts):
        raise ValueError("PREREQUISITE_INPUT_INVALID")
    claims = {
        concept_id: {
            claim["claim_id"]: set(claim["evidence_ids"])
            for claim in concept["claims"]
        }
        for concept_id, concept in concepts.items()
    }
    diagnostics = {
        "proposals": len(proposals),
        "verified_positive": 0,
        "not_positive": 0,
        "invalid": 0,
        "cycle_or_conflict": 0,
        "accepted": 0,
    }
    outgoing = {concept_id: set() for concept_id in concepts}
    accepted_pairs = set()
    constraints = []
    for proposal in sorted(
        proposals,
        key=lambda item: str(item.get("proposal_id")) if isinstance(item, dict) else "",
    ):
        if (
            not isinstance(proposal, dict)
            or set(proposal) != {
                "proposal_id", "source_formal_concept_id",
                "target_formal_concept_id", "evidence_bindings",
            }
            or not isinstance(proposal["proposal_id"], str)
            or not proposal["proposal_id"]
            or proposal["source_formal_concept_id"] not in concepts
            or proposal["target_formal_concept_id"] not in concepts
            or proposal["source_formal_concept_id"]
            == proposal["target_formal_concept_id"]
            or not isinstance(proposal["evidence_bindings"], list)
            or not proposal["evidence_bindings"]
        ):
            diagnostics["invalid"] += 1
            continue
        evidence_is_valid = True
        for binding in proposal["evidence_bindings"]:
            if (
                not isinstance(binding, dict)
                or set(binding) != {
                    "owner_formal_concept_id", "claim_id", "evidence_ids"
                }
                or binding["owner_formal_concept_id"] not in {
                    proposal["source_formal_concept_id"],
                    proposal["target_formal_concept_id"],
                }
                or binding["claim_id"]
                not in claims[binding["owner_formal_concept_id"]]
                or not isinstance(binding["evidence_ids"], list)
                or not binding["evidence_ids"]
                or binding["evidence_ids"] != sorted(set(binding["evidence_ids"]))
                or not set(binding["evidence_ids"])
                <= claims[binding["owner_formal_concept_id"]][binding["claim_id"]]
            ):
                evidence_is_valid = False
                break
        if not evidence_is_valid:
            diagnostics["invalid"] += 1
            continue
        try:
            is_positive = verifier(deepcopy(proposal))
        except Exception:
            is_positive = None
        if is_positive is not True:
            diagnostics["not_positive"] += 1
            continue
        diagnostics["verified_positive"] += 1
        source = proposal["source_formal_concept_id"]
        target = proposal["target_formal_concept_id"]
        if (
            (source, target) in accepted_pairs
            or (target, source) in accepted_pairs
            or _can_reach(outgoing, target, source)
        ):
            diagnostics["cycle_or_conflict"] += 1
            continue
        identity = {
            "source_formal_concept_id": source,
            "target_formal_concept_id": target,
            "evidence_bindings": deepcopy(proposal["evidence_bindings"]),
            "verifier_binding": {
                **deepcopy(verifier_binding),
                "outcome": "positive",
            },
        }
        constraints.append(
            {
                "prerequisite_constraint_id": (
                    "prerequisite-constraint:sha256:" + canonical_sha256(identity)
                ),
                **identity,
                "processing": "succeeded",
                "quality": "accepted",
                "decision": "retain",
                "reason_codes": [],
            }
        )
        outgoing[source].add(target)
        accepted_pairs.add((source, target))
    diagnostics["accepted"] = len(constraints)
    return constraints, diagnostics


def prerequisite_constraints_are_valid(
    constraints: Any, formal_concepts: list[dict[str, Any]]
) -> bool:
    try:
        concept_ids = {
            concept["formal_concept_id"] for concept in formal_concepts
        }
        claims = {
            concept["formal_concept_id"]: {
                claim["claim_id"]: set(claim["evidence_ids"])
                for claim in concept["claims"]
            }
            for concept in formal_concepts
        }
        outgoing = {concept_id: set() for concept_id in concept_ids}
        seen_ids = set()
        seen_pairs = set()
        for constraint in constraints:
            fields = {
                "prerequisite_constraint_id", "source_formal_concept_id",
                "target_formal_concept_id", "evidence_bindings",
                "verifier_binding", "processing", "quality", "decision",
                "reason_codes",
            }
            if not isinstance(constraint, dict) or set(constraint) != fields:
                return False
            source = constraint["source_formal_concept_id"]
            target = constraint["target_formal_concept_id"]
            verifier = constraint["verifier_binding"]
            if (
                source not in concept_ids or target not in concept_ids
                or source == target
                or constraint["prerequisite_constraint_id"] in seen_ids
                or (source, target) in seen_pairs or (target, source) in seen_pairs
                or set(verifier) != {"model_id", "revision", "policy", "outcome"}
                or verifier["outcome"] != "positive"
                or any(
                    not isinstance(verifier[field], str) or not verifier[field]
                    for field in ("model_id", "revision", "policy")
                )
                or not isinstance(constraint["evidence_bindings"], list)
                or not constraint["evidence_bindings"]
                or (constraint["processing"], constraint["quality"], constraint["decision"])
                != ("succeeded", "accepted", "retain")
                or constraint["reason_codes"] != []
                or _can_reach(outgoing, target, source)
            ):
                return False
            for binding in constraint["evidence_bindings"]:
                owner = binding["owner_formal_concept_id"]
                if (
                    set(binding) != {"owner_formal_concept_id", "claim_id", "evidence_ids"}
                    or owner not in {source, target}
                    or binding["claim_id"] not in claims[owner]
                    or binding["evidence_ids"] != sorted(set(binding["evidence_ids"]))
                    or not binding["evidence_ids"]
                    or not set(binding["evidence_ids"]) <= claims[owner][binding["claim_id"]]
                ):
                    return False
            identity = {
                "source_formal_concept_id": source,
                "target_formal_concept_id": target,
                "evidence_bindings": constraint["evidence_bindings"],
                "verifier_binding": verifier,
            }
            if constraint["prerequisite_constraint_id"] != (
                "prerequisite-constraint:sha256:" + canonical_sha256(identity)
            ):
                return False
            outgoing[source].add(target)
            seen_pairs.add((source, target))
            seen_ids.add(constraint["prerequisite_constraint_id"])
        return isinstance(constraints, list)
    except (KeyError, TypeError, ValueError):
        return False

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

@dataclass(frozen=True)
class EvidenceContext:
    evidence_id: str
    page: int
    quote: str
    source_locator: dict[str, Any]


@dataclass(frozen=True)
class ClaimContext:
    claim_id: str
    text: str
    evidence: tuple[EvidenceContext, ...]


@dataclass(frozen=True)
class ConceptContext:
    concept_id: str
    label: str
    claims: tuple[ClaimContext, ...]
    prerequisite_ids: tuple[str, ...]


@dataclass(frozen=True)
class MapContext:
    material_id: UUID
    knowledge_structure_revision: str
    concepts: tuple[ConceptContext, ...]
    initial_learning_path: tuple[str, ...]


def _context_from_validated_document(
    material_id: UUID, document: dict[str, Any]
) -> MapContext:
    evidence_by_id = {item["evidence_id"]: item for item in document["evidence"]}
    prerequisites: dict[str, list[str]] = {
        concept["concept_id"]: [] for concept in document["concepts"]
    }
    for relation in document["relations"]:
        if relation["type"] == "prerequisite":
            prerequisites[relation["target_concept_id"]].append(
                relation["source_concept_id"]
            )
    concepts = []
    for concept in document["concepts"]:
        claims = []
        for claim in concept["claims"]:
            quotes = {
                reference: " ".join(
                    span["quote"]
                    for span in claim["source_spans"]
                    if span["evidence_id"] == reference
                )
                for reference in claim["evidence_refs"]
            }
            claims.append(
                ClaimContext(
                    claim["claim_id"],
                    claim["text"],
                    tuple(
                        EvidenceContext(
                            reference,
                            evidence_by_id[reference]["page"],
                            quotes[reference],
                            evidence_by_id[reference]["source_locator"],
                        )
                        for reference in claim["evidence_refs"]
                    ),
                )
            )
        concepts.append(
            ConceptContext(
                concept["concept_id"],
                concept["label"],
                tuple(claims),
                tuple(prerequisites[concept["concept_id"]]),
            )
        )
    return MapContext(
        material_id,
        document["revision"],
        tuple(concepts),
        tuple(step["concept_id"] for step in document["initial_learning_path"]),
    )

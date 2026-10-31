"""以已保存的來源檢核完整教學單元；產生可審查投影，不改正式 KS／作答。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from content_identity import canonical_sha256
from .claim_literals import LITERAL_PROJECTION, EXCERPT_PROJECTION, EXCERPT_PROJECTIONS, transcript_evidence_ids
from .semantic_projection import _project_claim
from .structure_rules import (
    RELATION_BASIS,
    _coalesce_relations,
    _id,
    _path,
    _revision,
)
from .structure_validation import validate_knowledge_structure


POLICY = "material-review/v1"


class ReviewError(ValueError):
    pass


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Supported(_Closed):
    evidence: list[int] = Field(min_length=1)
    # 只擋重複失控；Luna 時期各類 reason 最長約 160 字。
    reason: str = Field(min_length=1, max_length=1000)


class Assignment(_Supported):
    concept: int
    action: Literal["keep", "group", "example", "metadata", "needs_review"]
    target: int | None
    issue: Literal[
        "none",
        "fragment",
        "case",
        "author",
        "layout",
        "scale",
        "incomplete",
        "uncertain",
    ]


class AliasEdit(_Supported):
    concept: int
    remove: list[str] = Field(min_length=1)


class ClaimEdit(_Supported):
    claim: int
    meaning: str = Field(min_length=1)


class RelationEdit(_Supported):
    relation: int
    action: Literal["reverse", "remove", "retype"]
    relation_type: (
        Literal["prerequisite", "part_of", "application", "example", "contrast"] | None
    )


class Proposal(_Closed):
    assignments: list[Assignment]
    alias_edits: list[AliasEdit]
    claim_edits: list[ClaimEdit]
    relation_edits: list[RelationEdit]


def response_schema() -> dict:
    return Proposal.model_json_schema()


def _name(value: str) -> str:
    # 僅用於名稱比對；不改寫原始字面值、Claims 或 Evidence。
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def is_spoken_claim(claim: dict) -> bool:
    """只對引用全為逐字稿的 Claim 套口語規則；同概念或同 Claim 混入 PDF 不擴張保護。"""
    return bool(claim["evidence"]) and all(
        "transcript" in source for source in claim["evidence"]
    )


@dataclass
class ReviewUnit:
    source_digest: str
    payload: dict
    concepts: list[dict]
    claims: list[dict]
    evidence: list[dict]
    relations: list[dict]
    conflicting_claim_ids: list[str] = field(default_factory=list)
    conflicting_relation_ids: list[str] = field(default_factory=list)


def _pack_review(
    view: dict, concepts: list[dict], evidence: list[dict], title: str
) -> ReviewUnit:
    selected = {concept["concept_id"] for concept in concepts}
    claims = list(
        {
            claim["claim_id"]: claim
            for concept in concepts
            for claim in concept["claims"]
        }.values()
    )
    relations = [
        relation
        for relation in view["relations"]
        if relation["source_concept_id"] in selected
        and relation["target_concept_id"] in selected
    ]
    concept_handles = {
        concept["concept_id"]: index for index, concept in enumerate(concepts)
    }
    claim_handles = {claim["claim_id"]: index for index, claim in enumerate(claims)}
    evidence_handles = {
        item["evidence_id"]: index for index, item in enumerate(evidence)
    }
    sections = {
        section["section_id"]: section["title"]
        for section in view["document_tree"]["sections"]
    }
    # 章節編號是版面導覽，概念名稱通常沒有它；只用於主題保護比對，不改原文或 IDs。
    section_titles = {
        _name(title)
        for section_title in sections.values()
        for title in (
            section_title,
            re.sub(r"^\s*\d+(?:\.\d+)*[.．、)]\s*(?=[^\d\s])", "", section_title),
        )
    }
    direction_rules = {
        "prerequisite": "必要先備→後續知識",
        "part_of": "部分→整體",
        "application": "概念→具體用途",
        "example": "抽象概念→具體實例",
        "contrast": "兩端自身是比較對象",
    }
    payload = {
        "policy": POLICY,
        "title": title,
        "concepts": [
            {
                "h": index,
                "label": concept["label"],
                "aliases": concept["aliases"],
                "claims": [
                    claim_handles[claim["claim_id"]] for claim in concept["claims"]
                ],
                "evidence": sorted(
                    {
                        evidence_handles[item["evidence_id"]]
                        for claim in concept["claims"]
                        for item in claim["evidence"]
                    }
                ),
                "section_titles": [
                    sections[section_id] for section_id in concept["section_ids"]
                ],
                "is_section_topic": _name(concept["label"]) in section_titles,
            }
            for index, concept in enumerate(concepts)
        ],
        "claims": [
            {
                "h": index,
                "text": claim["text"],
                "transcript_only": is_spoken_claim(claim),
                **({"quotes": [
                    {"e": evidence_handles[item["evidence_id"]], "q": item["quote"]}
                    for item in claim["evidence"]
                ]} if any("transcript" in item for item in claim["evidence"]) else {}),
                "evidence": [
                    evidence_handles[item["evidence_id"]] for item in claim["evidence"]
                ],
            }
            for index, claim in enumerate(claims)
        ],
        "evidence": [
            {
                "h": index,
                "source_name": item["source_name"],
                "page": item["normalized_page"],
                "kind": item["kind"],
                "text": item["quote"],
                **({"transcript": item["transcript"]} if "transcript" in item else {}),
            }
            for index, item in enumerate(evidence)
        ],
        "relations": [
            {
                "h": index,
                "source": concept_handles[relation["source_concept_id"]],
                "target": concept_handles[relation["target_concept_id"]],
                "source_label": concepts[
                    concept_handles[relation["source_concept_id"]]
                ]["label"],
                "target_label": concepts[
                    concept_handles[relation["target_concept_id"]]
                ]["label"],
                "direction_rule": direction_rules[relation["type"]],
                "type": relation["type"],
                "reason": relation["learner_reason"],
                "evidence": [
                    evidence_handles[reference]
                    for reference in relation["evidence_refs"]
                    if reference in evidence_handles
                ],
            }
            for index, relation in enumerate(relations)
        ],
    }
    spoken_sources = {
        item["source_id"]: item["transcript"]
        for item in evidence if "transcript" in item
    }
    if spoken_sources:
        payload["spoken_sources"] = spoken_sources
    return ReviewUnit(
        canonical_sha256(view),
        payload,
        deepcopy(concepts),
        deepcopy(claims),
        deepcopy(evidence),
        deepcopy(relations),
    )


def combine_reviews(
    view: dict, reviews: list[tuple[ReviewUnit, dict]]
) -> tuple[ReviewUnit, dict]:
    """彙整互不重疊的小節提案；衝突範圍不得以最後一份覆蓋前一份。"""
    selected = set()
    evidence = {}
    source_digest = canonical_sha256(view) if reviews else None
    for unit, response in reviews:
        if unit.source_digest != source_digest:
            raise ReviewError("REVIEW_SOURCE_CHANGED")
        validate_proposal(unit, response)
        concept_ids = {concept["concept_id"] for concept in unit.concepts}
        if selected & concept_ids:
            raise ReviewError("REVIEW_SCOPES_OVERLAP")
        selected.update(concept_ids)
        evidence.update({item["evidence_id"]: item for item in unit.evidence})
    if not selected:
        raise ReviewError("REVIEW_SCOPE_EMPTY")

    merged = _pack_review(
        view,
        [concept for concept in view["concepts"] if concept["concept_id"] in selected],
        sorted(
            evidence.values(),
            key=lambda item: (
                item["source_id"],
                item["normalized_page"],
                item["block_order"],
                item["evidence_id"],
            ),
        ),
        "已檢核小節的整體整理候選",
    )
    concept_handles = {
        concept["concept_id"]: index for index, concept in enumerate(merged.concepts)
    }
    claim_handles = {
        claim["claim_id"]: index for index, claim in enumerate(merged.claims)
    }
    evidence_handles = {
        item["evidence_id"]: index for index, item in enumerate(merged.evidence)
    }
    relation_handles = {
        relation["relation_id"]: index
        for index, relation in enumerate(merged.relations)
    }
    proposal = {
        key: []
        for key in ["assignments", "alias_edits", "claim_edits", "relation_edits"]
    }
    for unit, response in reviews:
        for kind, rows in response.items():
            for item in rows:
                row = deepcopy(item)
                if "concept" in row:
                    row["concept"] = concept_handles[
                        unit.concepts[row["concept"]]["concept_id"]
                    ]
                if row.get("target") is not None:
                    row["target"] = concept_handles[
                        unit.concepts[row["target"]]["concept_id"]
                    ]
                if "claim" in row:
                    row["claim"] = claim_handles[unit.claims[row["claim"]]["claim_id"]]
                if "relation" in row:
                    relation_id = unit.relations[row["relation"]]["relation_id"]
                    row["relation"] = relation_handles[relation_id]
                row["evidence"] = [
                    evidence_handles[unit.evidence[handle]["evidence_id"]]
                    for handle in row["evidence"]
                ]
                proposal[kind].append(row)
    # 概念範圍互不重疊，仍可能共用同一 canonical Claim。只協調跨段提案；
    # 單一模型回應內的 Claim 重複編號仍由 validate_proposal 拒絕。
    claim_edits = {}
    for row in proposal["claim_edits"]:
        claim_edits.setdefault(row["claim"], []).append(row)
    proposal["claim_edits"] = []
    for handle, rows in claim_edits.items():
        if len({row["meaning"] for row in rows}) != 1:
            merged.conflicting_claim_ids.append(merged.claims[handle]["claim_id"])
            continue
        row = deepcopy(rows[0])
        row["evidence"] = sorted({ref for edit in rows for ref in edit["evidence"]})
        row["reason"] = "\n".join(dict.fromkeys(edit["reason"] for edit in rows))
        proposal["claim_edits"].append(row)
    validate_proposal(merged, proposal)
    return merged, proposal


def validate_proposal(unit: ReviewUnit, value: dict) -> Proposal:
    try:
        proposal = Proposal.model_validate(value)
    except ValidationError:
        raise ReviewError("REVIEW_RESPONSE_INVALID") from None
    # 完全相同只保留一筆；關係的不同提案在來源檢查後整組略過。
    for name in ("alias_edits", "claim_edits", "relation_edits"):
        unique = {}
        for edit in getattr(proposal, name):
            unique.setdefault(canonical_sha256(edit.model_dump()), edit)
        setattr(proposal, name, list(unique.values()))
    # xgrammar 無法禁止陣列重複；同一別名列兩次的刪除不可靠（實例：想改標題譯名卻刪掉正確別名），
    # 只捨棄這筆別名修改、別名維持原樣，不讓整份審查失敗。
    proposal.alias_edits = [e for e in proposal.alias_edits if len(set(e.remove)) == len(e.remove)]
    assignments = {a.concept: a for a in proposal.assignments}
    if len(assignments) != len(proposal.assignments) or set(assignments) != set(
        range(len(unit.concepts))
    ):
        raise ReviewError("REVIEW_CONCEPT_COVERAGE_INVALID")
    evidence_handles = set(range(len(unit.evidence)))
    owned = [
        {
            item["evidence_id"]
            for claim in concept["claims"]
            for item in claim["evidence"]
        }
        for concept in unit.concepts
    ]

    def support(edit):
        if not set(edit.evidence) <= evidence_handles or not edit.reason.strip():
            raise ReviewError("REVIEW_EVIDENCE_INVALID")
        # 重複引用不增加來源語意；只在已解析提案中保序去重，保留原始模型回應供稽核。
        edit.evidence = list(dict.fromkeys(edit.evidence))
        return {unit.evidence[handle]["evidence_id"] for handle in edit.evidence}

    for a in proposal.assignments:
        refs = support(a)
        if not refs & owned[a.concept]:
            raise ReviewError("REVIEW_CONCEPT_SUPPORT_INVALID")
        if a.action in {"group", "example"}:
            if (
                a.target not in assignments
                or a.target == a.concept
                or assignments[a.target].action == "metadata"
            ):
                raise ReviewError("REVIEW_TARGET_INVALID")
        elif a.target is not None:
            raise ReviewError("REVIEW_TARGET_INVALID")
        if a.action == "metadata" and a.issue not in {"author", "layout", "scale"}:
            raise ReviewError("REVIEW_METADATA_REASON_INVALID")
    # 模型偶爾提出多層歸屬。循環無法解釋，拒絕；非循環的父節點則在投影時保留，
    # 不把整個章節沿著鏈條壓成一個過大的學習點。
    for key in assignments:
        seen = set()
        while assignments[key].action in {"group", "example"}:
            if key in seen:
                raise ReviewError("REVIEW_GROUPING_CYCLE")
            seen.add(key)
            key = assignments[key].target
    for changes, key, limit in [
        (proposal.alias_edits, "concept", len(unit.concepts)),
        (proposal.claim_edits, "claim", len(unit.claims)),
        (proposal.relation_edits, "relation", len(unit.relations)),
    ]:
        seen = set()
        for edit in changes:
            handle = getattr(edit, key)
            if handle not in range(limit) or (handle in seen and key != "relation"):
                raise ReviewError("REVIEW_EDIT_ID_INVALID")
            seen.add(handle)
            refs = support(edit)
            if key == "concept":
                if (
                    not set(edit.remove) <= set(unit.concepts[handle]["aliases"])
                    or not refs & owned[handle]
                ):
                    raise ReviewError("REVIEW_ALIAS_EDIT_INVALID")
            elif key == "claim" and not edit.meaning.strip():
                raise ReviewError("REVIEW_CLAIM_EDIT_INVALID")
            elif key == "relation":
                relation = unit.relations[handle]
                endpoints = {
                    relation["source_concept_id"],
                    relation["target_concept_id"],
                }
                endpoint_refs = {
                    item["evidence_id"]
                    for concept in unit.concepts
                    if concept["concept_id"] in endpoints
                    for claim in concept["claims"]
                    for item in claim["evidence"]
                }
                allowed_refs = endpoint_refs
                if edit.action == "remove":
                    # 移除錯掛關係時，同頁章節標題可證明真正的脈絡；仍須引用至少一端。
                    pages = {
                        (item["source_id"], item["normalized_page"])
                        for item in unit.evidence
                        if item["evidence_id"] in endpoint_refs
                    }
                    allowed_refs = endpoint_refs | {
                        item["evidence_id"]
                        for item in unit.evidence
                        if item["kind"] == "heading"
                        and (item["source_id"], item["normalized_page"]) in pages
                    }
                # reverse/remove 可以重述原型別，但不能同時偷偷更換型別。
                invalid_type = (
                    edit.relation_type is None
                    if edit.action == "retype"
                    else edit.relation_type not in (None, relation["type"])
                )
                if not refs & endpoint_refs or not refs <= allowed_refs or invalid_type:
                    raise ReviewError("REVIEW_RELATION_EDIT_INVALID")
    relation_edits = {}
    for edit in proposal.relation_edits:
        relation_edits.setdefault(edit.relation, []).append(edit)
    unit.conflicting_relation_ids = [
        unit.relations[handle]["relation_id"]
        for handle, edits in relation_edits.items() if len(edits) > 1
    ]
    proposal.relation_edits = [
        edits[0] for edits in relation_edits.values() if len(edits) == 1
    ]
    return proposal


def _corrected_claim(edit: ClaimEdit, sources: list[dict]) -> str | None:
    text = " ".join(item["quote"] for item in sources)
    meaning = " ".join(edit.meaning.split())
    words = lambda value: set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", value))
    if not words(meaning) <= words(text):
        return None
    evidence = {
        item["evidence_id"]: {"exact_text": item["quote"], "kind": item["kind"]}
        for item in sources
    }
    projected = _project_claim(
        {
            "meaning": meaning,
            "source_spans": [
                {"evidence_id": item["evidence_id"], "quote": item["quote"]}
                for item in sources
            ],
        },
        evidence,
    )
    return (
        projected["text"]
        if projected and projected["projection"] == LITERAL_PROJECTION
        else None
    )


def _claim_edit_sources(unit: ReviewUnit, edit: ClaimEdit) -> list[dict]:
    """核對套用後會保留的原引用與補正引用，不把原引用冒稱為模型新選取。"""
    evidence = {item["evidence_id"]: item for item in unit.evidence}
    originals = unit.claims[edit.claim]["evidence"]
    original = {item["evidence_id"] for item in originals}
    selected = [unit.evidence[h]["evidence_id"] for h in edit.evidence]
    return [*originals, *(
        evidence[identity] for identity in dict.fromkeys(selected) if identity not in original
    )]


def transcript_restore_text(unit: ReviewUnit, claim: int, meaning: str) -> str | None:
    """疑字不能猜正字；只容許撤回候選改述，還原該 Claim 已保存的原文節錄。"""
    original = " ".join(item["quote"] for item in unit.claims[claim]["evidence"])
    # PDF 可能在詞內換行。只忽略排版空白核對還原意圖；原 quote 不動，學習文字沿用既有空白正規化。
    if not isinstance(meaning, str) or "".join(meaning.split()) != "".join(original.split()):
        return None
    return original


def project_review(view: dict, unit: ReviewUnit, value: dict) -> dict:
    """保留完整來源與原 IDs；投影是整理提案，不冒充原 revision 的新內容。"""
    if canonical_sha256(view) != unit.source_digest:
        raise ReviewError("REVIEW_SOURCE_CHANGED")
    proposal = validate_proposal(unit, value)
    concepts = {c["concept_id"]: c for c in view["concepts"]}
    teaching_root = {key: key for key in concepts}
    roles = {key: "keep" for key in concepts}
    blocked = [
        {"claim_id": claim_id, "reason": "CONFLICTING_REVIEW_EDITS_PRESERVED_ORIGINAL"}
        for claim_id in unit.conflicting_claim_ids
    ] + [
        {"relation_id": relation_id, "reason": "CONFLICTING_REVIEW_EDITS_PRESERVED_ORIGINAL"}
        for relation_id in unit.conflicting_relation_ids
    ]
    findings = []
    assignments = []
    prerequisites = {
        relation[endpoint]
        for relation in view["relations"]
        if relation["type"] == "prerequisite"
        for endpoint in ["source_concept_id", "target_concept_id"]
    }
    # 檢核新提出的先備關係也必須保留兩端，不能同時把端點合併成同一個學習點。
    for edit in proposal.relation_edits:
        if edit.action == "retype" and edit.relation_type == "prerequisite":
            relation = unit.relations[edit.relation]
            prerequisites.update(
                (relation["source_concept_id"], relation["target_concept_id"])
            )
    parents = {
        assignment.target
        for assignment in proposal.assignments
        if assignment.action in {"group", "example"}
    }
    for a in proposal.assignments:
        key = unit.concepts[a.concept]["concept_id"]
        action = a.action
        if a.concept in parents and action in {"group", "example"}:
            blocked.append(
                {
                    "concept_id": key,
                    "reason": "PARENT_KEPT_AS_LEARNING_UNIT",
                    "proposed_action": action,
                }
            )
            action = "needs_review"
        if key in prerequisites and action in {"group", "example"}:
            blocked.append(
                {
                    "concept_id": key,
                    "reason": "PREREQUISITE_ENDPOINT_KEPT",
                    "proposed_action": action,
                }
            )
            action = "needs_review"
        if action == "metadata" and (
            unit.payload["concepts"][a.concept]["is_section_topic"]
            or key in prerequisites
        ):
            blocked.append(
                {
                    "concept_id": key,
                    "reason": "PROTECTED_TOPIC_OR_PREREQUISITE",
                    "proposed_action": action,
                }
            )
            action = "needs_review"
        roles[key] = action
        if action in {"group", "example"}:
            teaching_root[key] = unit.concepts[a.target]["concept_id"]
        if action == "needs_review" or (
            a.issue == "uncertain"
            and any(is_spoken_claim(claim) for claim in unit.concepts[a.concept]["claims"])
        ):
            findings.append({"concept_id": key, "reason": a.reason})
        target = unit.concepts[a.target] if a.target is not None else None
        assignments.append(
            {
                "concept_id": key,
                "action": action,
                "requested_action": a.action,
                "target_concept_id": (
                    target["concept_id"]
                    if target and action in {"group", "example"}
                    else None
                ),
                "requested_target_concept_id": target["concept_id"] if target else None,
                "reason": a.reason,
                "model_evidence_ids": [
                    unit.evidence[handle]["evidence_id"] for handle in a.evidence
                ],
                "source_evidence_ids": sorted(
                    {
                        item["evidence_id"]
                        for claim in unit.concepts[a.concept]["claims"]
                        for item in claim["evidence"]
                    }
                ),
                "target_evidence_ids": sorted(
                    {
                        item["evidence_id"]
                        for claim in target["claims"]
                        for item in claim["evidence"]
                    }
                )
                if target
                else [],
            }
        )
    units = []
    for key, concept in concepts.items():
        if roles[key] in {"group", "example", "metadata"}:
            continue
        members = [
            k for k in concepts if teaching_root[k] == key and roles[k] != "example"
        ]
        examples = [
            k for k in concepts if teaching_root[k] == key and roles[k] == "example"
        ]
        units.append(
            {
                "concept_id": key,
                "label": concept["label"],
                "aliases": list(concept["aliases"]),
                "member_concept_ids": members,
                "example_concept_ids": examples,
                "claim_ids": list(
                    dict.fromkeys(
                        claim["claim_id"]
                        for member in members
                        for claim in concepts[member]["claims"]
                    )
                ),
            }
        )
    by_unit = {u["concept_id"]: u for u in units}
    alias_changes = []
    for edit in proposal.alias_edits:
        original = unit.concepts[edit.concept]
        alias_changes.append(
            {
                "concept_id": original["concept_id"],
                "remove": edit.remove,
                "reason": edit.reason,
                "evidence_ids": [
                    unit.evidence[handle]["evidence_id"] for handle in edit.evidence
                ],
            }
        )
        if original["concept_id"] in by_unit:
            by_unit[original["concept_id"]]["aliases"] = [
                alias for alias in original["aliases"] if alias not in edit.remove
            ]
    # 改寫須通過字面值檢查；保留原文不算修正成功。
    claim_changes = []
    for edit in proposal.claim_edits:
        sources = _claim_edit_sources(unit, edit)
        claim_id = unit.claims[edit.claim]["claim_id"]
        source_text = _name(" ".join(item["quote"] for item in sources))
        meaning = _name(edit.meaning)
        owners = {
            concept["concept_id"]
            for concept in unit.concepts
            if any(claim["claim_id"] == claim_id for claim in concept["claims"])
        }
        uncertain = is_spoken_claim(unit.claims[edit.claim]) and any(
            unit.concepts[a.concept]["concept_id"] in owners
            and a.issue == "uncertain"
            for a in proposal.assignments
        )
        restored = transcript_restore_text(unit, edit.claim, edit.meaning) if uncertain else None
        if uncertain and restored is None:
            blocked.append({"claim_id": claim_id, "reason": "TRANSCRIPT_UNCERTAIN_PRESERVED_ORIGINAL"})
            continue
        introduced = [
            concept["label"]
            for concept in unit.concepts
            if concept["concept_id"] not in owners
            and _name(concept["label"]) in meaning
            and _name(concept["label"]) not in source_text
        ]
        if introduced:
            blocked.append(
                {
                    "claim_id": claim_id,
                    "reason": "CLAIM_REFERENT_NOT_CITED",
                    "concepts": introduced,
                }
            )
            continue
        corrected = restored if uncertain else _corrected_claim(edit, sources)
        if corrected is None:
            blocked.append(
                {"claim_id": claim_id, "reason": "CLAIM_CORRECTION_NOT_SUPPORTED"}
            )
        else:
            claim_changes.append(
                {
                    "original_claim_id": claim_id,
                    "proposed_text": corrected,
                    "evidence_ids": [
                        unit.evidence[h]["evidence_id"] for h in edit.evidence
                    ],
                    "reason": edit.reason,
                }
            )
    relation_changes = {
        unit.relations[edit.relation]["relation_id"]: edit
        for edit in proposal.relation_edits
    }
    relations, excluded = [], []
    for original in view["relations"]:
        row = deepcopy(original)
        evidence_ids = []
        edit = relation_changes.get(row["relation_id"])
        if edit:
            applied = True
            if edit.action == "remove":
                if row["type"] == "prerequisite":
                    blocked.append(
                        {
                            "relation_id": row["relation_id"],
                            "reason": "PREREQUISITE_REMOVAL_REQUIRES_REVIEW",
                        }
                    )
                    applied = False
                else:
                    excluded.append(
                        {"relation_id": row["relation_id"], "reason": edit.reason}
                    )
                    continue
            elif edit.action == "reverse":
                row["source_concept_id"], row["target_concept_id"] = (
                    row["target_concept_id"],
                    row["source_concept_id"],
                )
            else:
                if (
                    row["type"] == "prerequisite"
                    and edit.relation_type != "prerequisite"
                ):
                    blocked.append(
                        {
                            "relation_id": row["relation_id"],
                            "reason": "PREREQUISITE_REMOVAL_REQUIRES_REVIEW",
                        }
                    )
                    applied = False
                else:
                    row["type"] = edit.relation_type
            if applied:
                row["learner_reason"] = edit.reason
                evidence_ids = [unit.evidence[h]["evidence_id"] for h in edit.evidence]
        s, t = row["source_concept_id"], row["target_concept_id"]
        if roles[s] == "metadata" or roles[t] == "metadata":
            excluded.append(
                {"relation_id": row["relation_id"], "reason": "metadata_endpoint"}
            )
            continue
        # Teaching membership 只作 advisory；不得把關係兩端換成教學單元的根。
        # 原有錯向 example 診斷保留，但不因此內部化或丟棄 canonical relation。
        if row["type"] == "example" and roles[s] == "example" and teaching_root[s] == t:
            findings.append(
                {
                    "relation_id": row["relation_id"],
                    "reason": "EXAMPLE_DIRECTION_OR_TYPE_NEEDS_REVIEW",
                }
            )
        relations.append(
            {
                "original_relation_id": row["relation_id"],
                "source_concept_id": s,
                "target_concept_id": t,
                "type": row["type"],
                "reason": row["learner_reason"],
                "evidence_ids": evidence_ids,
            }
        )
    try:
        path = _path(units, relations)
    except ValueError:
        raise ReviewError("REVIEW_PREREQUISITE_CYCLE") from None
    metadata = [key for key in concepts if roles[key] == "metadata"]
    accounted = [
        key
        for review_unit in units
        for key in review_unit["member_concept_ids"]
        + review_unit["example_concept_ids"]
    ] + metadata
    if len(accounted) != len(set(accounted)) or set(accounted) != set(concepts):
        raise ReviewError("REVIEW_CONTENT_LOST")
    return {
        "schema": "material-review-projection/v1",
        "policy": POLICY,
        "source_revision": view["knowledge_structure_revision"],
        "source_sha256": unit.source_digest,
        "status": "needs_review",
        "publication_authorized": False,
        "learning_units": units,
        "metadata_concept_ids": metadata,
        "relations": relations,
        "assignments": assignments,
        "internalized_relations": [],
        "excluded_relations": excluded,
        "learning_path": path,
        "alias_changes": alias_changes,
        "claim_changes": claim_changes,
        "blocked_changes": blocked,
        "findings": findings,
        "preserved_claim_ids": sorted(
            {
                claim["claim_id"]
                for concept in concepts.values()
                for claim in concept["claims"]
            }
        ),
        "preserved_evidence_ids": sorted(
            {
                item["evidence_id"]
                for concept in concepts.values()
                for claim in concept["claims"]
                for item in claim["evidence"]
            }
        ),
        "checks": {
            "all_concepts_accounted_for": True,
            "source_unchanged": canonical_sha256(view) == unit.source_digest,
            "path_covers_all_learning_units": len(path) == len(units),
            "dangling_relations": 0,
        },
    }


def apply_review(
    document: dict, view: dict, unit: ReviewUnit, response: dict
) -> tuple[dict, dict]:
    """建立內容 hash 已重算的候選；呼叫端保存修正前後對照。"""
    if (
        not validate_knowledge_structure(document)
        or view["knowledge_structure_revision"] != document["revision"]
    ):
        raise ReviewError("REVIEW_SOURCE_CHANGED")
    projection = project_review(view, unit, response)
    result = deepcopy(document)
    evidence = {e["evidence_id"]: e for e in document["evidence"]}
    excerpt_evidence_ids = transcript_evidence_ids(document)
    edits = {e["original_claim_id"]: e for e in projection["claim_changes"]}
    remap, claim_remap, concepts = {}, {}, []
    rejected_claim_edits = set()
    alias_removals = {
        edit["concept_id"]: set(edit["remove"]) for edit in projection["alias_changes"]
    }
    metadata = set(projection["metadata_concept_ids"])
    # Canonical 內容逐個原概念套用 edits；group/example 不授權 many-to-one identity contraction。
    for concept in document["concepts"]:
        if concept["concept_id"] in metadata:
            continue
        claims = {}
        for original in concept["claims"]:
            claim = deepcopy(original)
            if edit := edits.get(claim["claim_id"]):
                # 原引用仍保留；補正的引用只能來自原文 Evidence。
                refs = list(
                    dict.fromkeys(claim["evidence_refs"] + edit["evidence_ids"])
                )
                excerpts = claim["projection"] in EXCERPT_PROJECTIONS
                spans = deepcopy(claim["source_spans"]) if excerpts else []
                spans.extend([
                    {"evidence_id": ref, "quote": evidence[ref]["exact_text"]}
                    for ref in refs
                    if not excerpts or ref not in claim["evidence_refs"]
                ])
                projected = _project_claim(
                    {
                        "meaning": edit["proposed_text"],
                        "source_spans": spans,
                    },
                    evidence,
                    excerpts=excerpts,
                    excerpt_evidence_ids=excerpt_evidence_ids,
                )
                if projected is None or projected["projection"] not in {LITERAL_PROJECTION, EXCERPT_PROJECTION}:
                    rejected_claim_edits.add(original["claim_id"])
                else:
                    claim.update(projected, evidence_refs=refs)
                    claim["claim_id"] = _id(
                        "claim",
                        {
                            key: value
                            for key, value in claim.items()
                            if key != "claim_id"
                        },
                    )
            claim_remap[original["claim_id"]] = claim["claim_id"]
            claims[claim["claim_id"]] = claim
        refs = list(
            dict.fromkeys(e for q in claims.values() for e in q["evidence_refs"])
        )
        aliases = sorted(
            set(concept["aliases"]) - alias_removals.get(concept["concept_id"], set())
        )
        identity = {
            "label": concept["label"],
            "aliases": aliases,
            "claim_ids": list(claims),
            "evidence_refs": refs,
        }
        key = _id("concept", identity)
        remap[concept["concept_id"]] = key
        concepts.append(
            {
                "concept_id": key,
                "label": concept["label"],
                "aliases": aliases,
                "claims": list(claims.values()),
                "evidence_refs": refs,
                "section_ids": list(
                    dict.fromkeys(evidence[e]["section_id"] for e in refs)
                ),
                "source_pages": sorted({evidence[e]["page"] for e in refs}),
            }
        )
    order = {e: i for i, e in enumerate(evidence)}
    concepts.sort(
        key=lambda c: (min(order[e] for e in c["evidence_refs"]), c["concept_id"])
    )
    original_relations = {r["relation_id"]: r for r in document["relations"]}
    relations = []
    for proposed in projection["relations"]:
        row = deepcopy(original_relations[proposed["original_relation_id"]])
        row.update(
            source_concept_id=remap[proposed["source_concept_id"]],
            target_concept_id=remap[proposed["target_concept_id"]],
            type=proposed["type"],
            learner_reason=proposed["reason"],
            inference_basis=RELATION_BASIS[proposed["type"]],
        )
        # 保留原關係的支持，並帶入本次實際套用的修正引用及其章節。
        row["evidence_refs"] = list(
            dict.fromkeys(row["evidence_refs"] + proposed["evidence_ids"])
        )
        row["context_refs"] = list(
            dict.fromkeys(
                row["context_refs"]
                + [evidence[ref]["section_id"] for ref in proposed["evidence_ids"]]
            )
        )
        relations.append(row)
    try:
        relations = _coalesce_relations(relations)
    except ValueError:
        raise ReviewError("REVIEW_RELATION_CONFLICT") from None
    result.update(
        concepts=concepts,
        relations=relations,
        initial_learning_path=_path(concepts, relations),
    )
    for key in sorted(rejected_claim_edits):
        projection["blocked_changes"].append(
            {"claim_id": key, "reason": "CANONICAL_LITERAL_GUARD"}
        )
    projection["claim_changes"] = [
        edit
        for edit in projection["claim_changes"]
        if edit["original_claim_id"] not in rejected_claim_edits
        and edit["original_claim_id"] in claim_remap
    ]
    for section in result["document_tree"]["sections"]:
        section["concept_ids"] = [
            concept["concept_id"]
            for concept in concepts
            if concept["section_ids"][0] == section["section_id"]
        ]
    if projection["findings"] or projection["blocked_changes"]:
        result["source_review_required"] = True
        reasons = result["status"]["reason_codes"]
        if "SOURCE_REVIEW_SUGGESTED" not in reasons:
            reasons.append("SOURCE_REVIEW_SUGGESTED")
        result["status"].update(
            processing="partial", quality="needs_review", decision="review"
        )
    result["revision"] = _revision(result)
    if not validate_knowledge_structure(result):
        raise ReviewError("REVIEW_STRUCTURE_INVALID")
    projection["applied_revision"] = result["revision"]
    projection["concept_mapping"] = remap
    projection["claim_mapping"] = claim_remap
    return result, projection

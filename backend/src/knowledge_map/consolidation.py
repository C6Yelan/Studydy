"""合併等義概念與重複敘述；每筆原 Claim 的來源必須完整留下。"""

from copy import deepcopy
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .claim_literals import (
    LITERAL_PROJECTION, EXCERPT_PROJECTION, EXCERPT_PROJECTIONS,
    literal_differences, preserves_literals, quoted_sources,
)
from .material_review import ReviewError
from .semantic_projection import _project_claim
from .structure_rules import _coalesce_relations, _id, _path, _revision
from .structure_validation import validate_knowledge_structure


POLICY = "source-preserving-consolidation/v1"


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Candidate(_Closed):
    concepts: list[int] = Field(min_length=2)
    reason: str = Field(min_length=1)


class Candidates(_Closed):
    groups: list[Candidate]


class Claim(_Closed):
    claims: list[int] = Field(min_length=1)
    # null 表示原樣保留單一 Claim；不要求模型重抄所有沒有變動的文字。
    text: str | None


class Concept(_Closed):
    concepts: list[int] = Field(min_length=1)
    title_from: int
    claims: list[Claim] = Field(min_length=1)
    reason: str = Field(min_length=1)


class Proposal(_Closed):
    concepts: list[Concept]


class Check(_Closed):
    concept: int
    equivalent_concepts: bool
    preserves_unique_information: bool
    sources_support_text: bool
    duplicates_removed: bool
    reason: str = Field(min_length=1)


class Checks(_Closed):
    checks: list[Check]


def pack(document, handles):
    """只使用已保存的 Claims／Evidence；短目錄不可作合併的事實依據。"""
    concepts = document["concepts"]
    claims = []
    rows = []
    refs = []
    for handle in handles:
        concept = concepts[handle]
        owned = []
        for claim in concept["claims"]:
            owned.append(len(claims))
            claims.append(deepcopy(claim))
            refs.extend(claim["evidence_refs"])
        rows.append(
            {
                "h": handle,
                "label": concept["label"],
                "aliases": concept["aliases"],
                "claims": owned,
            }
        )
    refs = list(dict.fromkeys(refs))
    evidence = {e["evidence_id"]: e for e in document["evidence"]}
    sources = {
        s["source_id"]: s["original_name"]
        for s in document["input_binding"]["manifest"]["items"]
    }
    pages = document["input_binding"]["bundle"]["pages"]
    positions = {ref: i for i, ref in enumerate(refs)}
    payload = {
        "concepts": rows,
        "claims": [
            {
                "h": i,
                "text": q["text"],
                "evidence": [positions[r] for r in q["evidence_refs"]],
                **({"quotes": [
                    {"e": positions[span["evidence_id"]], "q": span["quote"]}
                    for span in q["source_spans"]
                ]} if q["projection"] in EXCERPT_PROJECTIONS else {}),
            }
            for i, q in enumerate(claims)
        ],
        "evidence": [],
    }
    for ref in refs:
        item = evidence[ref]
        page = pages[item["page"] - 1]
        payload["evidence"].append(
            {
                "h": positions[ref],
                "text": item["exact_text"],
                "source_id": page["source_id"],
                "source_name": sources[page["source_id"]],
                "page": page["normalized_page"],
                "block": item["block_order"],
                "kind": item["kind"],
            }
        )
    return payload, claims


def _partition(actual, expected, code):
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        error = ReviewError(code)
        error.details = {
            "expected_handles": list(expected),
            "missing_handles": sorted(set(expected) - set(actual)),
            "unexpected_handles": sorted(set(actual) - set(expected)),
            "duplicate_handles": sorted(
                h for h, count in Counter(actual).items() if count > 1
            ),
        }
        raise error


def project(document, handles, response):
    """結構與字面值先檢核；等義與語意完整性另交獨立模型檢核。"""
    try:
        proposal = Proposal.model_validate(response)
    except ValidationError:
        raise ReviewError("CONSOLIDATION_RESPONSE_INVALID") from None
    payload, originals = pack(document, handles)
    owners = {c["h"]: c["claims"] for c in payload["concepts"]}
    _partition(
        [h for c in proposal.concepts for h in c.concepts],
        handles,
        "CONSOLIDATION_CONCEPT_COVERAGE_INVALID",
    )
    evidence = {e["evidence_id"]: e for e in document["evidence"]}
    changes, literal_issues = [], []
    for concept in proposal.concepts:
        if concept.title_from not in concept.concepts:
            raise ReviewError("CONSOLIDATION_TITLE_INVALID")
        try:
            _partition(
                [h for q in concept.claims for h in q.claims],
                [h for c in concept.concepts for h in owners[c]],
                "CONSOLIDATION_CLAIM_COVERAGE_INVALID",
            )
        except ReviewError as error:
            error.details["concepts"] = concept.concepts
            raise
        claims, mapping = [], {}
        for edit in concept.claims:
            original_claims = [originals[h] for h in edit.claims]
            if edit.text is None:
                if len(original_claims) != 1:
                    raise ReviewError("CONSOLIDATION_TEXT_REQUIRED")
                claim = deepcopy(original_claims[0])
            elif len(original_claims) == 1 and edit.text == original_claims[0]["text"]:
                claim = deepcopy(original_claims[0])
            else:
                refs = list(
                    dict.fromkeys(
                        r for q in original_claims for r in q["evidence_refs"]
                    )
                )
                excerpts = any(q["projection"] in EXCERPT_PROJECTIONS for q in original_claims)
                spans = []
                for original in original_claims:
                    for span in original["source_spans"]:
                        if span not in spans:
                            spans.append(deepcopy(span))
                sources = quoted_sources(spans, evidence, excerpts=excerpts)
                projected = _project_claim(
                    {
                        "meaning": edit.text,
                        "source_spans": spans,
                    },
                    evidence,
                    excerpts=excerpts,
                )
                if (
                    projected is None
                    or projected["projection"] not in {LITERAL_PROJECTION, EXCERPT_PROJECTION}
                    or not preserves_literals(
                        edit.text, [{"exact_text": q["text"]} for q in original_claims]
                    )
                ):
                    original_texts = [q["text"] for q in original_claims] + [
                        source["exact_text"] for source in sources
                    ]
                    missing, _ = literal_differences(
                        edit.text, [{"exact_text": text} for text in original_texts]
                    )
                    _, unexpected = literal_differences(
                        edit.text, sources
                    )
                    literal_issues.append(
                        {
                            "concepts": concept.concepts,
                            "claims": edit.claims,
                            "missing_literals": sorted(missing),
                            "unexpected_literals": sorted(unexpected),
                            "instruction": "保留來源及原 Claim 的全部數值、條件、公式與程式碼，包含章節號、表號及實驗設定。"
                            "缺漏值須放回原有對象與脈絡，不能只在末尾列數字補過檢查；code/formula 須逐字保留。",
                        }
                    )
                    continue
                claim = {**projected, "evidence_refs": refs}
                claim["claim_id"] = _id("claim", claim)
            claims.append(claim)
            mapping.update({q["claim_id"]: claim["claim_id"] for q in original_claims})
        # 同一 Claim 可能已由不同概念共用；合併後只保留一份，來源未少。
        claims = list({q["claim_id"]: q for q in claims}.values())
        label = document["concepts"][concept.title_from]["label"]
        aliases = sorted(
            {
                name
                for h in concept.concepts
                for name in [
                    document["concepts"][h]["label"],
                    *document["concepts"][h]["aliases"],
                ]
            }
            - {label}
        )
        refs = list(dict.fromkeys(r for q in claims for r in q["evidence_refs"]))
        identity = {
            "label": label,
            "aliases": aliases,
            "claim_ids": [q["claim_id"] for q in claims],
            "evidence_refs": refs,
        }
        row = {
            "concept_id": _id("concept", identity),
            "label": label,
            "aliases": aliases,
            "claims": claims,
            "evidence_refs": refs,
            "section_ids": list(dict.fromkeys(evidence[r]["section_id"] for r in refs)),
            "source_pages": sorted({evidence[r]["page"] for r in refs}),
        }
        changes.append(
            {
                "members": concept.concepts,
                "concept": row,
                "claim_mapping": mapping,
                "reason": concept.reason,
            }
        )
    if literal_issues:
        error = ReviewError("CONSOLIDATION_LITERAL_LOSS")
        error.details = {"claims": literal_issues}
        raise error
    return changes


def apply(document, changes):
    """重算新 revision 的所有內容 ID；舊版、Evidence 與來源集合均不改寫。"""
    result = deepcopy(document)
    replaced = {h for group in changes for h in group["members"]}
    if len(replaced) != sum(len(group["members"]) for group in changes):
        raise ReviewError("CONSOLIDATION_CONCEPT_COVERAGE_INVALID")
    concepts = [
        deepcopy(c) for h, c in enumerate(document["concepts"]) if h not in replaced
    ]
    concepts.extend(deepcopy(group["concept"]) for group in changes)
    remap = {c["concept_id"]: c["concept_id"] for c in document["concepts"]}
    for group in changes:
        for h in group["members"]:
            remap[document["concepts"][h]["concept_id"]] = group["concept"][
                "concept_id"
            ]
    order = {e["evidence_id"]: i for i, e in enumerate(document["evidence"])}
    concepts.sort(
        key=lambda c: (min(order[r] for r in c["evidence_refs"]), c["concept_id"])
    )
    relations, internalized = [], []
    for original in document["relations"]:
        row = deepcopy(original)
        row["source_concept_id"] = remap[row["source_concept_id"]]
        row["target_concept_id"] = remap[row["target_concept_id"]]
        if row["source_concept_id"] == row["target_concept_id"]:
            internalized.append(original["relation_id"])
            continue
        relations.append(row)
    try:
        relations = _coalesce_relations(relations)
    except ValueError:
        raise ReviewError("CONSOLIDATION_RELATION_CONFLICT") from None
    try:
        path = _path(concepts, relations)
    except ValueError:
        raise ReviewError("CONSOLIDATION_PREREQUISITE_CYCLE") from None
    result.update(concepts=concepts, relations=relations, initial_learning_path=path)
    for section in result["document_tree"]["sections"]:
        section["concept_ids"] = [
            c["concept_id"]
            for c in concepts
            if c["section_ids"][0] == section["section_id"]
        ]
    result["revision"] = _revision(result)
    if not validate_knowledge_structure(result):
        raise ReviewError("CONSOLIDATION_STRUCTURE_INVALID")
    return result, {"concept_mapping": remap, "internalized_relations": internalized}

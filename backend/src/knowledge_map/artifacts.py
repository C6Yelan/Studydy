from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from pdf_evidence.study_material_output import (
    PAGE_CONTENT_EXCLUDED,
    validate_known_limitations,
    validate_study_material_output,
)


RELATION_TYPES = frozenset(
    {"prerequisite", "contains", "application", "example"}
)


class KnowledgeMapError(ValueError):
    """讓 CLI 與測試可取得穩定的失敗原因。"""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical_sha256(value: Any) -> str:
    """用固定 JSON 表示計算跨次執行一致的 identity。"""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise KnowledgeMapError("KNOWLEDGE_MAP_CANONICALIZATION_FAILED") from error
    return hashlib.sha256(encoded).hexdigest()


def _with_id(prefix: str, field: str, content: dict[str, Any]) -> dict[str, Any]:
    return {field: f"{prefix}:sha256:{_canonical_sha256(content)}", **content}


def _with_revision(prefix: str, content: dict[str, Any]) -> dict[str, Any]:
    return {"revision": f"{prefix}:sha256:{_canonical_sha256(content)}", **content}


def _source_status(source: dict[str, Any]) -> dict[str, str]:
    """保留上游 partial/needs_review，不把有效建圖誤報為完整成功。"""
    if source["processing"] == "succeeded" and source["quality"] == "accepted":
        return {
            "processing": "succeeded",
            "quality": "accepted",
            "decision": "retain",
            "reason_code": "KNOWLEDGE_MAP_ACCEPTED",
        }
    return {
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_code": "SOURCE_OUTPUT_NEEDS_REVIEW",
    }


def _concept_evidence(concepts: list[dict[str, Any]]) -> dict[str, set[str]]:
    return {
        concept["concept_id"]: {
            evidence_id
            for member in concept["members"]
            for evidence_id in member["evidence_ids"]
        }
        for concept in concepts
    }


def _is_grounded(clue: dict[str, Any], evidence: dict[str, set[str]]) -> bool:
    """確認線索同時引用兩個端點的 Evidence。"""
    source_evidence = evidence.get(clue["source_concept_id"])
    target_evidence = evidence.get(clue["target_concept_id"])
    cited = set(clue["evidence_ids"])
    return (
        source_evidence is not None
        and target_evidence is not None
        and bool(cited & source_evidence)
        and bool(cited & target_evidence)
    )


def _relations_from_clues(
    source: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """只把方向與 Evidence 都成立的 direct clue 提升為正式 Relation。"""
    evidence = _concept_evidence(source["concepts"])
    relations = []
    review_items = []
    for clue in source["relation_clues"]:
        if not _is_grounded(clue, evidence):
            raise KnowledgeMapError("RELATION_EVIDENCE_INVALID")
        is_direct_clue = clue["kind"] in RELATION_TYPES
        if is_direct_clue and clue["direction_hint"] == "source_to_target":
            content = {
                "schema": "relation/v1",
                "type": clue["kind"],
                "source_concept_id": clue["source_concept_id"],
                "target_concept_id": clue["target_concept_id"],
                "statement": clue["statement"],
                "evidence_ids": sorted(clue["evidence_ids"]),
                "processing": "succeeded",
                "quality": "accepted",
                "decision": "retain",
                "reason_code": "DIRECT_CLUE_ACCEPTED",
            }
            relations.append(_with_id("relation", "relation_id", content))
            continue

        if clue["kind"] == "contrast":
            reason_code = "CONTRAST_REQUIRES_REVIEW"
        elif clue["kind"] == "sequence":
            reason_code = "SEQUENCE_NOT_PREREQUISITE"
        elif is_direct_clue:
            reason_code = "RELATION_DIRECTION_NEEDS_REVIEW"
        else:
            reason_code = "CLUE_KIND_REQUIRES_REVIEW"
        content = {
            "kind": clue["kind"],
            "source_concept_id": clue["source_concept_id"],
            "target_concept_id": clue["target_concept_id"],
            "statement": clue["statement"],
            "evidence_ids": sorted(clue["evidence_ids"]),
            "quality": "needs_review",
            "reason_code": reason_code,
        }
        review_items.append(_with_id("relation-review", "review_id", content))

    relations.sort(key=lambda item: item["relation_id"])
    review_items.sort(key=lambda item: item["review_id"])
    return relations, review_items


def validate_knowledge_map(knowledge_map: Any) -> str | None:
    """檢查 Relation 六類契約、Evidence grounding 與 revision。"""
    fields = {
        "schema",
        "revision",
        "source_output_id",
        "material_ref",
        "pages",
        "concepts",
        "evidence_index",
        "relations",
        "review_items",
        "known_limitations",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    if not isinstance(knowledge_map, dict) or set(knowledge_map) != fields:
        return "KNOWLEDGE_MAP_ROOT_INVALID"
    if knowledge_map["schema"] != "knowledge-map/v1":
        return "KNOWLEDGE_MAP_ROOT_INVALID"
    content = {key: value for key, value in knowledge_map.items() if key != "revision"}
    try:
        expected_revision = "knowledge-map:sha256:" + _canonical_sha256(content)
    except KnowledgeMapError:
        return "KNOWLEDGE_MAP_REVISION_INVALID"
    if knowledge_map["revision"] != expected_revision:
        return "KNOWLEDGE_MAP_REVISION_INVALID"

    concepts = knowledge_map["concepts"]
    if (
        not isinstance(concepts, list)
        or not concepts
        or any(not isinstance(concept, dict) for concept in concepts)
    ):
        return "KNOWLEDGE_MAP_CONCEPT_INVALID"
    concept_ids = [concept.get("concept_id") for concept in concepts]
    if (
        any(not isinstance(concept_id, str) for concept_id in concept_ids)
        or len(concept_ids) != len(set(concept_ids))
        or concepts != sorted(concepts, key=lambda concept: concept["concept_id"])
    ):
        return "KNOWLEDGE_MAP_CONCEPT_INVALID"
    try:
        evidence_by_concept = _concept_evidence(concepts)
        concept_page_refs = (
            {
                member["page_ref"]
                for concept in concepts
                for member in concept["members"]
            }
            if knowledge_map["known_limitations"]
            else set()
        )
    except (KeyError, TypeError):
        return "KNOWLEDGE_MAP_CONCEPT_INVALID"

    expected_source_status, limitation_reason = validate_known_limitations(
        material_ref=knowledge_map["material_ref"],
        pages=knowledge_map["pages"],
        known_limitations=knowledge_map["known_limitations"],
        concept_page_refs=concept_page_refs,
    )
    if limitation_reason is not None:
        return "KNOWLEDGE_MAP_LIMITATION_INVALID"
    expected_map_status = (
        (
            "succeeded",
            "accepted",
            "retain",
            "KNOWLEDGE_MAP_ACCEPTED",
        )
        if not knowledge_map["known_limitations"]
        else (
            "partial",
            "needs_review",
            "review",
            "SOURCE_OUTPUT_NEEDS_REVIEW",
        )
    )
    actual_map_status = tuple(
        knowledge_map[field]
        for field in ("processing", "quality", "decision", "reason_code")
    )
    if expected_source_status is None or actual_map_status != expected_map_status:
        return "KNOWLEDGE_MAP_LIMITATION_INVALID"

    evidence_index = knowledge_map["evidence_index"]
    if not isinstance(evidence_index, list):
        return "KNOWLEDGE_MAP_EVIDENCE_INVALID"
    evidence_ids = [item.get("evidence_id") for item in evidence_index]
    if (
        any(not isinstance(evidence_id, str) for evidence_id in evidence_ids)
        or len(evidence_ids) != len(set(evidence_ids))
        or set(evidence_ids) != set().union(*evidence_by_concept.values())
    ):
        return "KNOWLEDGE_MAP_EVIDENCE_INVALID"

    concept_id_set = set(concept_ids)
    relation_fields = {
        "relation_id",
        "schema",
        "type",
        "source_concept_id",
        "target_concept_id",
        "statement",
        "evidence_ids",
        "processing",
        "quality",
        "decision",
        "reason_code",
    }
    relation_ids = set()
    relation_semantic_keys = set()
    for relation in knowledge_map["relations"]:
        if not isinstance(relation, dict):
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        if set(relation) != relation_fields:
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        if (
            relation["schema"] != "relation/v1"
            or relation["type"] not in RELATION_TYPES
        ):
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        if (
            relation["source_concept_id"] not in concept_id_set
            or relation["target_concept_id"] not in concept_id_set
            or relation["source_concept_id"] == relation["target_concept_id"]
        ):
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        content = {
            key: value for key, value in relation.items() if key != "relation_id"
        }
        semantic_key = (
            relation["type"],
            relation["source_concept_id"],
            relation["target_concept_id"],
        )
        if (
            relation["relation_id"]
            != "relation:sha256:" + _canonical_sha256(content)
            or relation["relation_id"] in relation_ids
            or semantic_key in relation_semantic_keys
        ):
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        if not _is_grounded(relation, evidence_by_concept):
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        if (
            relation["processing"],
            relation["quality"],
            relation["decision"],
            relation["reason_code"],
        ) != ("succeeded", "accepted", "retain", "DIRECT_CLUE_ACCEPTED"):
            return "KNOWLEDGE_MAP_RELATION_INVALID"
        relation_ids.add(relation["relation_id"])
        relation_semantic_keys.add(semantic_key)
    if knowledge_map["relations"] != sorted(
        knowledge_map["relations"], key=lambda item: item["relation_id"]
    ):
        return "KNOWLEDGE_MAP_RELATION_INVALID"

    review_fields = {
        "review_id",
        "kind",
        "source_concept_id",
        "target_concept_id",
        "statement",
        "evidence_ids",
        "quality",
        "reason_code",
    }
    for item in knowledge_map["review_items"]:
        if not isinstance(item, dict):
            return "KNOWLEDGE_MAP_REVIEW_INVALID"
        if set(item) != review_fields:
            return "KNOWLEDGE_MAP_REVIEW_INVALID"
        if (
            item["source_concept_id"] not in concept_id_set
            or item["target_concept_id"] not in concept_id_set
            or item["source_concept_id"] == item["target_concept_id"]
        ):
            return "KNOWLEDGE_MAP_REVIEW_INVALID"
        if item["quality"] != "needs_review":
            return "KNOWLEDGE_MAP_REVIEW_INVALID"
        content = {key: value for key, value in item.items() if key != "review_id"}
        if item["review_id"] != "relation-review:sha256:" + _canonical_sha256(
            content
        ):
            return "KNOWLEDGE_MAP_REVIEW_INVALID"
        if not _is_grounded(item, evidence_by_concept):
            return "KNOWLEDGE_MAP_REVIEW_INVALID"
    return None


def build_knowledge_map(source: Any) -> dict[str, Any]:
    """由公開且已驗證的 Study Material Output 建立 Knowledge Map。"""
    reason = validate_study_material_output(source)
    if reason is not None:
        raise KnowledgeMapError(reason)
    relations, review_items = _relations_from_clues(source)
    content = {
        "schema": "knowledge-map/v1",
        "source_output_id": source["output_id"],
        "material_ref": source["material_ref"],
        "pages": deepcopy(source["pages"]),
        "concepts": sorted(deepcopy(source["concepts"]), key=lambda item: item["concept_id"]),
        "evidence_index": deepcopy(source["evidence_index"]),
        "relations": relations,
        "review_items": review_items,
        "known_limitations": deepcopy(source["known_limitations"]),
        **_source_status(source),
    }
    knowledge_map = _with_revision("knowledge-map", content)
    reason = validate_knowledge_map(knowledge_map)
    if reason is not None:
        raise KnowledgeMapError(reason)
    return knowledge_map


def build_initial_learning_path(knowledge_map: Any) -> dict[str, Any]:
    """只依 accepted prerequisite 做穩定拓撲排序；cycle 不回傳半條路徑。"""
    reason = validate_knowledge_map(knowledge_map)
    if reason is not None:
        raise KnowledgeMapError(reason)
    concept_ids = [concept["concept_id"] for concept in knowledge_map["concepts"]]
    learning_order_by_concept = {
        concept["concept_id"]: (
            min(member["page_number"] for member in concept["members"]),
            concept["concept_id"],
        )
        for concept in knowledge_map["concepts"]
    }
    next_concepts = {concept_id: [] for concept_id in concept_ids}
    incoming_count = {concept_id: 0 for concept_id in concept_ids}
    for relation in knowledge_map["relations"]:
        if relation["type"] != "prerequisite":
            continue
        source_id = relation["source_concept_id"]
        target_id = relation["target_concept_id"]
        if target_id not in next_concepts[source_id]:
            next_concepts[source_id].append(target_id)
            incoming_count[target_id] += 1

    ready = sorted(
        learning_order_by_concept[concept_id]
        for concept_id, count in incoming_count.items()
        if count == 0
    )
    ordered_concept_ids = []
    while ready:
        _, concept_id = ready.pop(0)
        ordered_concept_ids.append(concept_id)
        for target_id in sorted(next_concepts[concept_id]):
            incoming_count[target_id] -= 1
            if incoming_count[target_id] == 0:
                ready.append(learning_order_by_concept[target_id])
                ready.sort()

    if len(ordered_concept_ids) != len(concept_ids):
        ordered_concept_ids = []
        status = {
            "processing": "failed",
            "quality": "unsupported",
            "decision": "reject",
            "reason_code": "PREREQUISITE_CYCLE",
        }
    elif knowledge_map["quality"] == "accepted":
        status = {
            "processing": "succeeded",
            "quality": "accepted",
            "decision": "retain",
            "reason_code": "INITIAL_PATH_ACCEPTED",
        }
    else:
        status = {
            "processing": "succeeded",
            "quality": "needs_review",
            "decision": "review",
            "reason_code": "INITIAL_PATH_SOURCE_NEEDS_REVIEW",
        }
    content = {
        "schema": "initial-learning-path/v1",
        "knowledge_map_revision": knowledge_map["revision"],
        "material_ref": knowledge_map["material_ref"],
        "ordered_concept_ids": ordered_concept_ids,
        **status,
    }
    return _with_revision("initial-learning-path", content)


def build_knowledge_map_view(
    knowledge_map: Any, learning_path: Any
) -> dict[str, Any]:
    """只映射 React consumer 顯示、選取與 Evidence 回查所需欄位。"""
    reason = validate_knowledge_map(knowledge_map)
    if reason is not None:
        raise KnowledgeMapError(reason)
    if (
        not isinstance(learning_path, dict)
        or learning_path.get("knowledge_map_revision") != knowledge_map["revision"]
        or learning_path.get("material_ref") != knowledge_map["material_ref"]
        or learning_path.get("revision")
        != "initial-learning-path:sha256:"
        + _canonical_sha256(
            {key: value for key, value in learning_path.items() if key != "revision"}
        )
    ):
        raise KnowledgeMapError("INITIAL_PATH_REVISION_MISMATCH")

    evidence_by_id = {
        item["evidence_id"]: item for item in knowledge_map["evidence_index"]
    }
    sorted_concepts = sorted(
        knowledge_map["concepts"],
        key=lambda concept: (
            min(member["page_number"] for member in concept["members"]),
            concept["concept_id"],
        ),
    )
    concepts = []
    for index, concept in enumerate(sorted_concepts):
        members = sorted(
            concept["members"],
            key=lambda member: (member["page_number"], member["candidate_id"]),
        )
        evidence_ids = sorted(
            {
                evidence_id
                for member in members
                for evidence_id in member["evidence_ids"]
            }
        )
        concepts.append(
            {
                "id": concept["concept_id"],
                "label": concept["normalized_name"],
                "definition": members[0]["definition"],
                "members": [
                    {
                        "name": member["name"],
                        "definition": member["definition"],
                        "page_number": member["page_number"],
                    }
                    for member in members
                ],
                "evidence": [deepcopy(evidence_by_id[item]) for item in evidence_ids],
                "position": {"x": (index % 4) * 300, "y": (index // 4) * 180},
                "quality": concept["quality"],
                "reason_code": concept["reason_code"],
            }
        )

    def mapped_item(item: dict[str, Any], id_field: str) -> dict[str, Any]:
        return {
            "id": item[id_field],
            "source": item["source_concept_id"],
            "target": item["target_concept_id"],
            "statement": item["statement"],
            "evidence": [
                deepcopy(evidence_by_id[evidence_id])
                for evidence_id in item["evidence_ids"]
            ],
            "reason_code": item["reason_code"],
        }

    relations = []
    for relation in knowledge_map["relations"]:
        item = mapped_item(relation, "relation_id")
        item["type"] = relation["type"]
        relations.append(item)
    review_items = []
    for review in knowledge_map["review_items"]:
        item = mapped_item(review, "review_id")
        item["kind"] = review["kind"]
        review_items.append(item)

    page_number_by_ref = {
        page["page_ref"]: page["page_number"] for page in knowledge_map["pages"]
    }
    limitations = []
    for item in knowledge_map["known_limitations"]:
        page_numbers = (
            [page["page_number"] for page in item["affected_pages"]]
            if item["reason_code"] == PAGE_CONTENT_EXCLUDED
            else [
                page_number_by_ref[page_ref]
                for page_ref in item["affected_page_refs"]
            ]
        )
        limitations.append(
            {
                "reason_code": item["reason_code"],
                "page_numbers": sorted(page_numbers),
                "affected_page_count": len(page_numbers),
            }
        )
    return {
        "schema": "knowledge-map-view/v1",
        "material_ref": knowledge_map["material_ref"],
        "knowledge_map_revision": knowledge_map["revision"],
        "learning_path_revision": learning_path["revision"],
        "status": {
            key: knowledge_map[key]
            for key in ("processing", "quality", "decision", "reason_code")
        },
        "concepts": concepts,
        "relations": relations,
        "review_items": review_items,
        "path": {
            "ordered_concept_ids": learning_path["ordered_concept_ids"],
            **{
                key: learning_path[key]
                for key in ("processing", "quality", "decision", "reason_code")
            },
        },
        "limitations": limitations,
    }


def build_artifacts(source: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """一次建立互相以 revision 綁定的 Map、Path 與前端 view。"""
    knowledge_map = build_knowledge_map(source)
    learning_path = build_initial_learning_path(knowledge_map)
    return (
        knowledge_map,
        learning_path,
        build_knowledge_map_view(knowledge_map, learning_path),
    )


def write_fixture_artifacts(selection_path: Path, output_directory: Path) -> list[Path]:
    """驗證 selection 中每個 exact SHA，再原子寫入 derived artifacts。"""
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if (
        not isinstance(selection, dict)
        or set(selection) != {"schema", "fixtures"}
        or selection["schema"] != "knowledge-map-fixture-selection/v1"
        or not isinstance(selection["fixtures"], list)
        or not selection["fixtures"]
    ):
        raise KnowledgeMapError("FIXTURE_SELECTION_INVALID")
    output_directory.mkdir(parents=True, exist_ok=True)
    written_paths = []
    for fixture in selection["fixtures"]:
        relative_path = fixture.get("relative_path")
        if not isinstance(relative_path, str) or Path(relative_path).name != relative_path:
            raise KnowledgeMapError("FIXTURE_SELECTION_INVALID")
        source_bytes = (selection_path.parent / relative_path).read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != fixture.get("file_sha256"):
            raise KnowledgeMapError("FIXTURE_SHA256_MISMATCH")
        source = json.loads(source_bytes)
        if (
            source.get("output_id") != fixture.get("output_id")
            or source.get("material_ref") != fixture.get("material_ref")
            or len(source.get("pages", [])) != fixture.get("page_count")
        ):
            raise KnowledgeMapError("FIXTURE_IDENTITY_MISMATCH")
        knowledge_map, learning_path, view = build_artifacts(source)
        stem = relative_path.removesuffix("-study-material-output.json")
        artifacts = {
            f"{stem}-knowledge-map.json": knowledge_map,
            f"{stem}-initial-learning-path.json": learning_path,
            f"{stem}-knowledge-map-view.json": view,
        }
        for filename, artifact in artifacts.items():
            destination = output_directory / filename
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
            written_paths.append(destination)
    return written_paths


def main(arguments: list[str] | None = None) -> int:
    """提供不依賴 API 或資料庫的本機 fixture 重播入口。"""
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 2:
        print(
            "usage: python -m knowledge_map.artifacts SELECTION_JSON OUTPUT_DIR",
            file=sys.stderr,
        )
        return 2
    try:
        paths = write_fixture_artifacts(Path(arguments[0]), Path(arguments[1]))
    except (KnowledgeMapError, OSError, json.JSONDecodeError) as error:
        reason = error.reason_code if isinstance(error, KnowledgeMapError) else type(error).__name__
        print(reason, file=sys.stderr)
        return 1
    print(json.dumps({"written": [str(path) for path in paths]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

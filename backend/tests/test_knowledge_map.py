from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from knowledge_map.artifacts import (
    KnowledgeMapError,
    RELATION_TYPES,
    _with_id,
    _with_revision,
    build_artifacts,
    build_initial_learning_path,
    build_knowledge_map,
    build_knowledge_map_view,
    validate_knowledge_map,
    write_fixture_artifacts,
)


EXPECTED_FILES = {
    "selection.json": "614a5b836bf1491b87bb0f6c61779e5453bb05e930775667140f0e7e375550a2",
    "finance-01547e2c-study-material-output.json": "1313d59a0ad7afe61c96b5463902b8d8935c228ef0bf02d1caa2b96e2049c727",
    "programming-07b1c1c1-study-material-output.json": "271c23aee1e85dc6beed70760ed0bf49c013a64a36de786c464a5179c8ce5c6e",
}


def _fixture_directory() -> Path:
    """真實 fixture 位置由 Gate 明確指定，避免測試讀取其他教材。"""
    return Path(os.environ["STUDYDY_KNOWLEDGE_MAP_FIXTURES"])


def _source(filename: str) -> dict:
    return json.loads((_fixture_directory() / filename).read_text(encoding="utf-8"))


def _rebind_output_id(source: dict) -> None:
    content = {key: value for key, value in source.items() if key != "output_id"}
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    source["output_id"] = "study-material-output:sha256:" + hashlib.sha256(encoded).hexdigest()


def _rebind_map(knowledge_map: dict) -> dict:
    return _with_revision(
        "knowledge-map",
        {key: value for key, value in knowledge_map.items() if key != "revision"},
    )


def _relation(
    template: dict, relation_type: str, source_id: str, target_id: str, evidence_ids: list[str]
) -> dict:
    content = {
        **{key: value for key, value in template.items() if key != "relation_id"},
        "type": relation_type,
        "source_concept_id": source_id,
        "target_concept_id": target_id,
        "evidence_ids": evidence_ids,
    }
    return _with_id("relation", "relation_id", content)


def test_real_fixture_gate_builds_two_deterministic_artifact_sets(tmp_path):
    """鎖定 3/3 exact SHA，並以公開 output 完成 2/2 build。"""
    directory = _fixture_directory()
    assert {
        filename: hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        for filename in EXPECTED_FILES
    } == EXPECTED_FILES

    paths = write_fixture_artifacts(directory / "selection.json", tmp_path)

    assert len(paths) == 6
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
    first_bytes = {path.name: path.read_bytes() for path in paths}
    repeated = write_fixture_artifacts(directory / "selection.json", tmp_path)
    assert first_bytes == {path.name: path.read_bytes() for path in repeated}


def test_direct_clues_are_grounded_and_non_direct_clues_stay_reviewable():
    """contrast 不猜類型，sequence 不會悄悄變成 prerequisite。"""
    source = _source("programming-07b1c1c1-study-material-output.json")
    knowledge_map = build_knowledge_map(source)
    source_clues = source["relation_clues"]

    assert {relation["type"] for relation in knowledge_map["relations"]} == {
        "prerequisite",
        "contains",
        "application",
    }
    assert len(knowledge_map["relations"]) == sum(
        clue["kind"] in {"prerequisite", "contains", "application", "example"}
        and clue["direction_hint"] == "source_to_target"
        for clue in source_clues
    )
    assert {
        item["reason_code"] for item in knowledge_map["review_items"]
    } == {"CONTRAST_REQUIRES_REVIEW", "SEQUENCE_NOT_PREREQUISITE"}
    assert all(
        relation["type"] != "prerequisite"
        for relation in knowledge_map["relations"]
        if relation["statement"]
        in {clue["statement"] for clue in source_clues if clue["kind"] == "sequence"}
    )


@pytest.mark.parametrize(
    ("clue_kind", "reason_code"),
    [
        ("contrast", "CONTRAST_REQUIRES_REVIEW"),
        ("sequence", "SEQUENCE_NOT_PREREQUISITE"),
        ("diagram_connection", "CLUE_KIND_REQUIRES_REVIEW"),
    ],
)
def test_clue_only_observations_never_become_formal_relations(
    clue_kind, reason_code
):
    """三種 clue-only observation 只能進入待複核清單。"""
    source = _source("programming-07b1c1c1-study-material-output.json")
    clue = next(
        item for item in source["relation_clues"] if item["kind"] == "application"
    )
    clue["kind"] = clue_kind
    _rebind_output_id(source)

    knowledge_map = build_knowledge_map(source)

    assert clue["statement"] not in {
        relation["statement"] for relation in knowledge_map["relations"]
    }
    review = next(
        item
        for item in knowledge_map["review_items"]
        if item["statement"] == clue["statement"]
    )
    assert review["kind"] == clue_kind
    assert review["reason_code"] == reason_code


def test_relation_contract_accepts_only_the_six_frozen_types():
    knowledge_map = build_knowledge_map(
        _source("programming-07b1c1c1-study-material-output.json")
    )
    template = knowledge_map["relations"][0]
    for relation_type in RELATION_TYPES:
        changed = deepcopy(knowledge_map)
        changed["relations"] = [
            _relation(
                template,
                relation_type,
                template["source_concept_id"],
                template["target_concept_id"],
                template["evidence_ids"],
            )
        ]
        assert validate_knowledge_map(_rebind_map(changed)) is None

    changed = deepcopy(knowledge_map)
    changed["relations"] = [
        _relation(
            template,
            "related",
            template["source_concept_id"],
            template["target_concept_id"],
            template["evidence_ids"],
        )
    ]
    assert validate_knowledge_map(_rebind_map(changed)) == "KNOWLEDGE_MAP_RELATION_INVALID"


@pytest.mark.parametrize("relation_type", ["similar", "confusing"])
def test_resource_relation_types_flow_from_validated_source(relation_type):
    source = _source("programming-07b1c1c1-study-material-output.json")
    clue = next(
        item for item in source["relation_clues"] if item["kind"] == "application"
    )
    clue["kind"] = relation_type
    clue["direction_hint"] = "source_to_target"
    _rebind_output_id(source)

    knowledge_map = build_knowledge_map(source)

    assert any(
        relation["type"] == relation_type
        and relation["statement"] == clue["statement"]
        for relation in knowledge_map["relations"]
    )


def test_semantic_duplicate_relations_fail_closed():
    """同類型與端點的第二條 Relation 不得以不同 identity 重複出現。"""
    knowledge_map = build_knowledge_map(
        _source("programming-07b1c1c1-study-material-output.json")
    )
    first = knowledge_map["relations"][0]
    duplicate_content = {
        **{key: value for key, value in first.items() if key != "relation_id"},
        "statement": first["statement"] + "（另一個敘述）",
    }
    second = _with_id("relation", "relation_id", duplicate_content)
    semantic_key = ("type", "source_concept_id", "target_concept_id")

    assert first["relation_id"] != second["relation_id"]
    assert tuple(first[key] for key in semantic_key) == tuple(
        second[key] for key in semantic_key
    )
    changed = deepcopy(knowledge_map)
    changed["relations"] = [second]
    assert validate_knowledge_map(_rebind_map(changed)) is None

    changed["relations"] = sorted(
        [first, second], key=lambda relation: relation["relation_id"]
    )
    assert validate_knowledge_map(_rebind_map(changed)) == "KNOWLEDGE_MAP_RELATION_INVALID"


def test_wrong_direction_is_reviewed_instead_of_accepted():
    source = _source("programming-07b1c1c1-study-material-output.json")
    clue = next(item for item in source["relation_clues"] if item["kind"] == "application")
    clue["direction_hint"] = "bidirectional"
    _rebind_output_id(source)

    knowledge_map = build_knowledge_map(source)

    assert clue["statement"] not in {
        relation["statement"] for relation in knowledge_map["relations"]
    }
    review = next(
        item for item in knowledge_map["review_items"] if item["statement"] == clue["statement"]
    )
    assert review["reason_code"] == "RELATION_DIRECTION_NEEDS_REVIEW"


def test_only_prerequisites_order_the_path_and_cycle_returns_no_partial_path():
    knowledge_map = build_knowledge_map(
        _source("programming-07b1c1c1-study-material-output.json")
    )
    path = build_initial_learning_path(knowledge_map)
    prerequisite = next(
        relation for relation in knowledge_map["relations"] if relation["type"] == "prerequisite"
    )
    assert path["ordered_concept_ids"].index(prerequisite["source_concept_id"]) < path[
        "ordered_concept_ids"
    ].index(prerequisite["target_concept_id"])

    first, second = knowledge_map["concepts"][:2]
    evidence_ids = [
        first["members"][0]["evidence_ids"][0],
        second["members"][0]["evidence_ids"][0],
    ]
    template = knowledge_map["relations"][0]
    changed = deepcopy(knowledge_map)
    changed["relations"] = sorted(
        [
            _relation(template, "prerequisite", first["concept_id"], second["concept_id"], evidence_ids),
            _relation(template, "prerequisite", second["concept_id"], first["concept_id"], evidence_ids),
        ],
        key=lambda item: item["relation_id"],
    )
    cycle_path = build_initial_learning_path(_rebind_map(changed))

    assert cycle_path["ordered_concept_ids"] == []
    assert (
        cycle_path["processing"],
        cycle_path["quality"],
        cycle_path["decision"],
        cycle_path["reason_code"],
    ) == ("failed", "unsupported", "reject", "PREREQUISITE_CYCLE")


def test_zero_indegree_concepts_follow_earliest_material_page():
    """同時可學的 Concept 依教材最早頁次排列，不以 concept_id 取代頁序。"""
    knowledge_map = build_knowledge_map(
        _source("programming-07b1c1c1-study-material-output.json")
    )
    page_ordered_concept_ids = [
        concept["concept_id"]
        for concept in sorted(
            knowledge_map["concepts"],
            key=lambda concept: (
                min(member["page_number"] for member in concept["members"]),
                concept["concept_id"],
            ),
        )
    ]
    assert page_ordered_concept_ids != sorted(page_ordered_concept_ids)

    changed = deepcopy(knowledge_map)
    changed["relations"] = []
    path = build_initial_learning_path(_rebind_map(changed))

    assert path["ordered_concept_ids"] == page_ordered_concept_ids


def test_view_mapping_and_revisions_are_stable_and_bound():
    source = _source("finance-01547e2c-study-material-output.json")
    first = build_artifacts(source)
    second = build_artifacts(source)
    knowledge_map, path, view = first

    assert first == second
    assert view["schema"] == "knowledge-map-view/v1"
    assert view["knowledge_map_revision"] == knowledge_map["revision"]
    assert view["learning_path_revision"] == path["revision"]
    assert view["status"]["quality"] == "needs_review"
    unavailable = next(
        item for item in view["limitations"] if item["reason_code"] == "CONCEPT_CONTEXT_UNAVAILABLE"
    )
    assert unavailable["affected_page_count"] == 13
    for index, concept in enumerate(view["concepts"]):
        source_concept = next(item for item in source["concepts"] if item["concept_id"] == concept["id"])
        members = sorted(
            source_concept["members"],
            key=lambda member: (member["page_number"], member["candidate_id"]),
        )
        assert concept["label"] == source_concept["normalized_name"]
        assert concept["definition"] == members[0]["definition"]
        assert concept["members"] == [
            {
                "name": member["name"],
                "definition": member["definition"],
                "page_number": member["page_number"],
            }
            for member in members
        ]
        assert concept["position"] == {"x": (index % 4) * 300, "y": (index // 4) * 180}

    mismatched = deepcopy(path)
    mismatched["knowledge_map_revision"] = "knowledge-map:sha256:" + "0" * 64
    with pytest.raises(KnowledgeMapError, match="INITIAL_PATH_REVISION_MISMATCH"):
        build_knowledge_map_view(knowledge_map, mismatched)


def test_invalid_source_and_fixture_sha_fail_closed(tmp_path):
    source = _source("programming-07b1c1c1-study-material-output.json")
    source["concepts"][0]["members"][0]["evidence_ids"] = ["unknown"]
    with pytest.raises(KnowledgeMapError, match="STUDY_MATERIAL_OUTPUT"):
        build_knowledge_map(source)

    directory = _fixture_directory()
    selection = json.loads((directory / "selection.json").read_text(encoding="utf-8"))
    selection["fixtures"][0]["file_sha256"] = "0" * 64
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    for fixture in EXPECTED_FILES:
        if fixture != "selection.json":
            (tmp_path / fixture).write_bytes((directory / fixture).read_bytes())
    with pytest.raises(KnowledgeMapError, match="FIXTURE_SHA256_MISMATCH"):
        write_fixture_artifacts(selection_path, tmp_path / "output")

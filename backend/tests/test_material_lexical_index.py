from __future__ import annotations

import unicodedata
from copy import deepcopy

import pytest

from material_lexical_index import (
    HAN_RANGES,
    HAN_RANGE_POLICY_UNICODE_VERSION,
    build_material_lexical_index,
    generate_lexical_keys,
)
from material_normalized_blocks import (
    NORMALIZED_BLOCKS_STABLE_PATH,
    SCHEMA_VERSION as NORMALIZED_BLOCKS_SCHEMA_VERSION,
)


def _normalization_handoff() -> dict:
    return {
        "selection_run_schema_version": "material-selection-run/v1",
        "selection_run_status": "success",
        "selection_run_locator": "run:synthetic:selection",
        "normalized_blocks_schema_version": NORMALIZED_BLOCKS_SCHEMA_VERSION,
        "normalized_blocks_locator": NORMALIZED_BLOCKS_STABLE_PATH,
    }


def _block(
    block_id: str,
    text: object = "教材API_v2",
    *,
    page: int = 1,
    selected: bool = True,
) -> dict:
    block = {
        "material_id": "material:synthetic",
        "case_id": "case:synthetic",
        "artifact_ref": "artifact:synthetic",
        "block_id": block_id,
        "locator": {
            "pdf_page": page,
            "source_ref": f"source:{page}",
        },
        "provenance": {"native_analysis": {"library": "synthetic"}},
        "native_analysis_status": "success" if selected else "failed",
        "selection_status": "selected" if selected else "failed",
        "reasons": [] if selected else ["selection_failed"],
        "warnings": [],
    }
    if selected or text is not None:
        block["text"] = text
    return block


def _normalized(*blocks: dict) -> dict:
    return {
        "schema_version": "normalized-material-blocks/v1",
        "status": "success",
        "source_provenance": {
            "material_blocks": {"schema_version": "material-blocks/v1"},
            "native_analysis": {
                "schema_version": "material-native-analysis/v1"
            },
        },
        "materials": [
            {
                "material_id": "material:synthetic",
                "case_id": "case:synthetic",
                "artifact_ref": "artifact:synthetic",
                "blocks": list(blocks),
            }
        ],
    }


def _entry(result: dict, key: str) -> dict:
    return next(entry for entry in result["entries"] if entry["key"] == key)


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_keys(child))
    return keys


@pytest.mark.parametrize(("start", "end"), HAN_RANGES)
def test_every_frozen_han_range_includes_both_boundaries(
    start: int,
    end: int,
) -> None:
    first = chr(start)
    last = chr(end)
    normalized_first = unicodedata.normalize("NFC", first)
    normalized_last = unicodedata.normalize("NFC", last)

    assert generate_lexical_keys(first) == [normalized_first]
    assert generate_lexical_keys(last) == [normalized_last]
    assert generate_lexical_keys(first + last) == [
        normalized_first + normalized_last
    ]


def test_han_runs_emit_overlapping_bigrams_and_singletons_at_boundaries() -> None:
    assert generate_lexical_keys("教材圖") == ["教材", "材圖"]
    assert generate_lexical_keys("教 A 材") == ["教", "a", "材"]
    assert generate_lexical_keys("\u4dbf\u4e00") == ["\u4dbf\u4e00"]


def test_ascii_technical_runs_and_nfc_use_the_frozen_rules() -> None:
    text = "API_v2 HTTP-2 foo.bar A..B _edge_ 教材API-v2 e\u0301"

    assert generate_lexical_keys(text) == [
        "api_v2",
        "http-2",
        "foo.bar",
        "a",
        "b",
        "edge",
        "教材",
        "api-v2",
    ]


def test_excluded_scripts_symbols_and_code_point_gaps_are_boundaries() -> None:
    excluded = (
        chr(0x33FF)
        + "あ한é"
        + chr(0x2A6E0)
        + chr(0x2EE5E)
        + chr(0x2FA20)
        + chr(0x3134B)
        + chr(0x3347A)
        + "🙂"
    )

    assert generate_lexical_keys(excluded) == []
    assert generate_lexical_keys("A" + excluded + "B") == ["a", "b"]


def test_lexical_keys_reject_non_string_input() -> None:
    with pytest.raises(ValueError, match="^lexical_text_invalid$"):
        generate_lexical_keys(3)


def test_index_entries_use_generated_lexical_keys() -> None:
    text = "演算法API_v2"
    result = build_material_lexical_index(
        _normalized(_block("block:1", text)),
        _normalization_handoff(),
    )

    assert [entry["key"] for entry in result["entries"]] == sorted(
        set(generate_lexical_keys(text))
    )


def test_duplicate_occurrences_are_counted_per_source_not_ranked() -> None:
    first = _block("block:2", "人人人 API API", page=2)
    second = _block("block:1", "人人 API", page=1)

    result = build_material_lexical_index(
        _normalized(first, second),
        _normalization_handoff(),
    )

    han_sources = _entry(result, "人人")["sources"]
    assert [
        (source["block_id"], source["occurrence_count"])
        for source in han_sources
    ] == [("block:1", 1), ("block:2", 2)]
    ascii_sources = _entry(result, "api")["sources"]
    assert [source["occurrence_count"] for source in ascii_sources] == [1, 2]
    assert not ({"score", "rank", "authority"} & _all_keys(result))


def test_material_and_block_permutations_produce_the_same_output() -> None:
    early = _block("block:early", "教材", page=1)
    late = _block("block:late", "API", page=2)
    first = _normalized(late, early)
    second = deepcopy(first)
    second["materials"][0]["blocks"].reverse()
    duplicate_material = deepcopy(second["materials"][0])
    duplicate_material["material_id"] = "material:z"
    duplicate_material["case_id"] = "case:z"
    duplicate_material["artifact_ref"] = "artifact:z"
    for block in duplicate_material["blocks"]:
        block["material_id"] = "material:z"
        block["case_id"] = "case:z"
        block["artifact_ref"] = "artifact:z"
    first["materials"].append(deepcopy(duplicate_material))
    second["materials"].insert(0, duplicate_material)

    assert build_material_lexical_index(
        first, _normalization_handoff()
    ) == build_material_lexical_index(second, _normalization_handoff())


def test_selected_sources_preserve_traceability_without_full_text() -> None:
    block = _block("block:1", "教材 API")
    block["warnings"] = ["z_warning", "a_warning", "z_warning"]
    block["selection_reason"] = "native_bbox_invalid"

    result = build_material_lexical_index(
        _normalized(block),
        _normalization_handoff(),
    )

    source = _entry(result, "教材")["sources"][0]
    assert source == {
        "material_id": "material:synthetic",
        "case_id": "case:synthetic",
        "artifact_ref": "artifact:synthetic",
        "block_id": "block:1",
        "locator": {"pdf_page": 1, "source_ref": "source:1"},
        "provenance": {"native_analysis": {"library": "synthetic"}},
        "native_analysis_status": "success",
        "selection_status": "selected",
        "reasons": [],
        "warnings": ["a_warning", "z_warning"],
        "selection_reason": "native_bbox_invalid",
        "occurrence_count": 1,
    }
    assert "text" not in _all_keys(result)


def test_omissions_handle_missing_source_fields_without_full_text() -> None:
    failed = _block("block:failed", None, page=2, selected=False)
    failed["material_id"] = None
    failed["locator"]["source_ref"] = None
    empty = _block("block:empty", "🙂 あ", page=1)

    result = build_material_lexical_index(
        _normalized(failed, empty),
        _normalization_handoff(),
    )

    assert result["status"] == "failed"
    assert result["reasons"] == [
        "no_indexable_lexical_units",
        "not_selected",
    ]
    assert [item["omission_reason"] for item in result["omissions"]] == [
        "no_indexable_lexical_units",
        "not_selected",
    ]
    assert result["omissions"][1]["material_id"] is None
    assert result["omissions"][1]["locator"]["source_ref"] is None
    assert "text" not in _all_keys(result)


def test_status_and_reason_rules_cover_success_partial_and_empty() -> None:
    success = build_material_lexical_index(
        _normalized(_block("block:key", "教材")),
        _normalization_handoff(),
    )
    partial = build_material_lexical_index(
        _normalized(
            _block("block:key", "教材"),
            _block("block:none", "🙂", page=2),
        ),
        _normalization_handoff(),
    )
    empty = build_material_lexical_index(
        _normalized(),
        _normalization_handoff(),
    )

    assert (success["status"], success["reasons"]) == ("success", [])
    assert (partial["status"], partial["reasons"]) == (
        "partial",
        ["no_indexable_lexical_units"],
    )
    assert (empty["status"], empty["reasons"]) == (
        "failed",
        ["no_indexable_lexical_units"],
    )


def test_inputs_are_unchanged_and_output_remains_auxiliary() -> None:
    normalized = _normalized(_block("block:1", "教材API"))
    normalization_handoff = _normalization_handoff()
    original_normalized = deepcopy(normalized)
    original_handoff = deepcopy(normalization_handoff)

    result = build_material_lexical_index(normalized, normalization_handoff)
    source = _entry(result, "教材")["sources"][0]
    source["provenance"]["native_analysis"]["library"] = "changed"

    assert normalized == original_normalized
    assert normalization_handoff == original_handoff
    assert HAN_RANGE_POLICY_UNICODE_VERSION == "17.0"
    assert result["policy"] == {
        "id": "han-bigram-access",
        "role": "auxiliary_lookup_only",
        "version": "v2",
    }
    assert result["provenance"] == {
        "normalized_blocks": {
            "locator": NORMALIZED_BLOCKS_STABLE_PATH,
            "schema_version": NORMALIZED_BLOCKS_SCHEMA_VERSION,
        },
        "normalization_handoff": {
            "selection_run_locator": "run:synthetic:selection",
            "selection_run_schema_version": "material-selection-run/v1",
            "selection_run_status": "success",
        },
    }


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("non_mapping", "normalization_handoff_invalid"),
        ("missing_fields", "normalization_handoff_invalid"),
        ("selection_run_schema", "selection_run_schema_mismatch"),
        ("selection_run_status", "selection_run_not_success"),
        ("selection_run_locator", "normalization_handoff_invalid"),
        (
            "normalized_schema",
            "normalized_blocks_handoff_schema_mismatch",
        ),
        (
            "normalized_locator",
            "normalized_blocks_handoff_locator_mismatch",
        ),
    ],
)
def test_every_normalization_handoff_failure_has_a_stable_error(
    mutation: str,
    expected: str,
) -> None:
    normalization_handoff: object = _normalization_handoff()
    if mutation == "non_mapping":
        normalization_handoff = []
    elif mutation == "missing_fields":
        normalization_handoff = {}
    elif mutation == "selection_run_schema":
        normalization_handoff["selection_run_schema_version"] = "wrong"
    elif mutation == "selection_run_status":
        normalization_handoff["selection_run_status"] = "failed"
    elif mutation == "selection_run_locator":
        normalization_handoff["selection_run_locator"] = ""
    elif mutation == "normalized_schema":
        normalization_handoff["normalized_blocks_schema_version"] = "wrong"
    else:
        normalization_handoff["normalized_blocks_locator"] = "wrong"

    with pytest.raises(ValueError, match=f"^{expected}$"):
        build_material_lexical_index(_normalized(), normalization_handoff)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("root", "normalized_blocks_invalid"),
        ("schema", "normalized_blocks_schema_mismatch"),
        ("materials", "normalized_blocks_materials_invalid"),
        ("material", "normalized_material_invalid"),
        ("blocks", "normalized_material_blocks_invalid"),
        ("block", "normalized_block_invalid"),
        ("locator", "normalized_block_locator_invalid"),
        ("text_missing", "selected_block_text_invalid"),
        ("text_non_string", "selected_block_text_invalid"),
        ("reasons", "normalized_block_reasons_invalid"),
    ],
)
def test_malformed_normalized_inputs_and_selected_text_fail_stably(
    mutation: str,
    expected: str,
) -> None:
    normalized: object = _normalized(_block("block:1"))
    if mutation == "root":
        normalized = []
    elif mutation == "schema":
        normalized["schema_version"] = "wrong"
    elif mutation == "materials":
        normalized["materials"] = {}
    elif mutation == "material":
        normalized["materials"][0] = []
    elif mutation == "blocks":
        normalized["materials"][0]["blocks"] = {}
    elif mutation == "block":
        normalized["materials"][0]["blocks"][0] = []
    elif mutation == "locator":
        normalized["materials"][0]["blocks"][0]["locator"] = []
    elif mutation == "text_missing":
        del normalized["materials"][0]["blocks"][0]["text"]
    elif mutation == "text_non_string":
        normalized["materials"][0]["blocks"][0]["text"] = 3
    else:
        normalized["materials"][0]["blocks"][0]["reasons"] = "wrong"

    with pytest.raises(ValueError, match=f"^{expected}$"):
        build_material_lexical_index(
            normalized,
            _normalization_handoff(),
        )

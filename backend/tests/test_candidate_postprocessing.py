from __future__ import annotations

from copy import deepcopy

import pytest

from candidate_extraction import extract_candidates
from candidate_postprocessing import postprocess_candidates


def _candidate(word: str, evidence_id: str) -> dict:
    return {
        "candidate_id": f"candidate-{evidence_id}",
        "text": word,
        "start": 0,
        "end": len(word),
        "evidence_ref": {
            "material_id": "material-a",
            "block_id": evidence_id,
            "locator": {"pdf_page": 1},
        },
    }


def test_filters_only_whitespace_and_all_punctuation_or_symbol_candidates() -> None:
    result = {
        "candidates": [
            _candidate(" \t\n", "whitespace"),
            _candidate("，。", "punctuation"),
            _candidate("★＋", "symbol"),
            _candidate("資料庫", "chinese"),
            _candidate("API", "alphanumeric"),
            _candidate("C++", "technical"),
        ]
    }

    rows = postprocess_candidates(result)

    assert [row["word"] for row in rows] == ["API", "C++", "資料庫"]


def test_counts_raw_words_keeps_evidence_order_and_sorts_deterministically() -> None:
    candidates = [
        _candidate("資料庫", "database-first"),
        _candidate("API", "api-first"),
        _candidate("資料庫", "database-second"),
        _candidate("Api", "mixed-case"),
        _candidate("API", "api-second"),
        _candidate("C++", "technical"),
    ]

    rows = postprocess_candidates({"candidates": candidates})

    assert rows == [
        {
            "word": "API",
            "occurrence_count": 2,
            "evidence_refs": [
                candidates[1]["evidence_ref"],
                candidates[4]["evidence_ref"],
            ],
        },
        {
            "word": "資料庫",
            "occurrence_count": 2,
            "evidence_refs": [
                candidates[0]["evidence_ref"],
                candidates[2]["evidence_ref"],
            ],
        },
        {
            "word": "Api",
            "occurrence_count": 1,
            "evidence_refs": [candidates[3]["evidence_ref"]],
        },
        {
            "word": "C++",
            "occurrence_count": 1,
            "evidence_refs": [candidates[5]["evidence_ref"]],
        },
    ]
    assert postprocess_candidates({"candidates": candidates}) == rows


def test_input_and_raw_candidate_fields_are_not_mutated() -> None:
    extraction_result = {
        "schema_version": "candidate-extraction/v1",
        "material_accounting": [{"material_id": "material-a", "outcome": "processed"}],
        "coverage": {"materials": [1, 1], "candidate_evidence": [2, 2]},
        "candidates": [
            _candidate(" API", "leading-space"),
            _candidate("API", "plain"),
        ],
    }
    original = deepcopy(extraction_result)

    rows = postprocess_candidates(extraction_result)

    assert extraction_result == original
    assert rows == [
        {
            "word": " API",
            "occurrence_count": 1,
            "evidence_refs": [extraction_result["candidates"][0]["evidence_ref"]],
        },
        {
            "word": "API",
            "occurrence_count": 1,
            "evidence_refs": [extraction_result["candidates"][1]["evidence_ref"]],
        },
    ]
    assert set(rows[0]) == {"word", "occurrence_count", "evidence_refs"}


@pytest.mark.parametrize(
    ("extraction_result", "message"),
    [
        ([], "extraction_result must be a mapping"),
        ({}, "candidates must be a list"),
        ({"candidates": [None]}, "candidate must be a mapping"),
        ({"candidates": [{}]}, "candidate text must be a string"),
        (
            {"candidates": [{"text": "API"}]},
            "candidate evidence_ref must be a mapping",
        ),
    ],
)
def test_minimum_required_input_errors_are_clear(
    extraction_result: object,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        postprocess_candidates(extraction_result)


def test_extractor_to_postprocessor_keeps_raw_result_and_aggregates_words() -> None:
    material_blocks = {
        "schema_version": "material-blocks/v1",
        "materials": [
            {
                "material_id": "material-a",
                "input_status": "valid",
                "blocks": [
                    {
                        "block_id": "block-a",
                        "text": "資料庫 API 資料庫。",
                        "locator": {"pdf_page": 2, "source_ref": "slide:4"},
                        "parser_status": "success",
                    }
                ],
            }
        ],
    }
    extraction_result = extract_candidates(material_blocks)
    original = deepcopy(extraction_result)

    rows = postprocess_candidates(extraction_result)

    assert extraction_result == original
    assert [(row["word"], row["occurrence_count"]) for row in rows] == [
        ("資料庫", 2),
        ("API", 1),
    ]
    database_evidence = rows[0]["evidence_refs"]
    assert len(database_evidence) == 2
    assert all(
        evidence
        == {
            "material_id": "material-a",
            "block_id": "block-a",
            "locator": {"pdf_page": 2, "source_ref": "slide:4"},
        }
        for evidence in database_evidence
    )

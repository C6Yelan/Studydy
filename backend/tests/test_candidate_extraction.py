from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jieba
import pytest

import candidate_extraction as extraction


def _artifact(text: str = "資料庫管理系統， API。\n下一行") -> dict:
    return {
        "schema_version": "material-blocks/v1",
        "materials": [
            {
                "material_id": "material-a",
                "input_status": "valid",
                "blocks": [
                    {
                        "block_id": "block-a",
                        "text": text,
                        "locator": {"pdf_page": 3, "source_ref": "slide:7"},
                        "parser_status": "success",
                    }
                ],
            }
        ],
    }


def test_traditional_technical_terms_keep_offsets_evidence_and_reconstruction() -> None:
    source = "資料庫管理系統， API。\n下一行"

    result = extraction.extract_candidates(_artifact(source))

    assert result["schema_version"] == "candidate-extraction/v1"
    assert result["segmenter"] == {
        "distribution": "jieba",
        "version": "0.42.1",
        "dictionary": {
            "path": "data/jieba/dict.txt.big",
            "sha256": extraction.DICTIONARY_SHA256,
        },
        "settings": {"cut_all": False, "hmm": False},
    }
    assert "資料庫" in [item["text"] for item in result["candidates"]]
    assert "管理系統" in [item["text"] for item in result["candidates"]]
    assert "".join(item["text"] for item in result["candidates"]) == source
    assert all(
        source[item["start"] : item["end"]] == item["text"]
        for item in result["candidates"]
    )
    assert all(
        item["evidence_ref"]
        == {
            "material_id": "material-a",
            "block_id": "block-a",
            "locator": {"pdf_page": 3, "source_ref": "slide:7"},
        }
        for item in result["candidates"]
    )
    assert result["coverage"] == {
        "materials": [1, 1],
        "blocks": [1, 1],
        "code_points": [len(source), len(source)],
        "candidate_evidence": [len(result["candidates"]), len(result["candidates"])],
    }


def test_every_cut_uses_the_fixed_dictionary_and_disables_hmm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    original_cut = jieba.Tokenizer.cut

    def observed_cut(self, sentence, *args, **kwargs):
        calls.append(
            {
                "dictionary": Path(self.dictionary),
                "cut_all": kwargs.get("cut_all"),
                "hmm": kwargs.get("HMM"),
            }
        )
        return original_cut(self, sentence, *args, **kwargs)

    monkeypatch.setattr(jieba.Tokenizer, "cut", observed_cut)

    extraction.extract_candidates(_artifact())

    assert calls == [
        {
            "dictionary": extraction.DICTIONARY_PATH,
            "cut_all": False,
            "hmm": False,
        }
    ]


def test_whitespace_and_punctuation_are_retained_for_exact_reconstruction() -> None:
    source = "資料庫  ，\tAPI\n"

    result = extraction.extract_candidates(_artifact(source))
    pieces = [item["text"] for item in result["candidates"]]

    assert "".join(pieces) == source
    assert any(piece.isspace() for piece in pieces)
    assert "，" in pieces


def test_invalid_inputs_and_excluded_blocks_are_observable() -> None:
    with pytest.raises(
        extraction.CandidateExtractionFailure,
        match="material_blocks_schema_mismatch",
    ):
        extraction.extract_candidates({"schema_version": "wrong", "materials": []})

    missing_page = _artifact()
    missing_page["materials"][0]["blocks"][0]["locator"] = {}
    with pytest.raises(
        extraction.CandidateExtractionFailure,
        match="page_locator_missing",
    ):
        extraction.extract_candidates(missing_page)

    excluded = _artifact()
    block = excluded["materials"][0]["blocks"][0]
    block["parser_status"] = "failed"
    block["text"] = None
    result = extraction.extract_candidates(excluded)
    assert result["candidates"] == []
    assert result["block_accounting"] == [
        {
            "material_id": "material-a",
            "block_id": "block-a",
            "outcome": "excluded",
            "reason": "block_not_successful",
        }
    ]
    assert result["coverage"] == {
        "materials": [1, 1],
        "blocks": [0, 1],
        "code_points": [0, 0],
        "candidate_evidence": [0, 0],
    }


def test_dictionary_and_license_provenance_are_hash_bound() -> None:
    data_root = extraction.DICTIONARY_PATH.parent
    provenance = json.loads((data_root / "provenance.json").read_text(encoding="utf-8"))
    dictionary = data_root / provenance["asset"]
    license_path = data_root / provenance["license"]["file"]

    assert provenance["upstream_tag"] == "v0.42.1"
    assert provenance["upstream_revision"] == "1e20c89b66f56c9301b0feed211733ffaa1bd72a"
    assert provenance["license"]["id"] == "MIT"
    assert dictionary.stat().st_size == provenance["byte_size"] == 8583143
    assert hashlib.sha256(dictionary.read_bytes()).hexdigest() == provenance["sha256"]
    assert hashlib.sha256(license_path.read_bytes()).hexdigest() == provenance["license"][
        "sha256"
    ]


def test_dictionary_tampering_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed_dictionary = tmp_path / "dict.txt.big"
    changed_dictionary.write_text("資料庫 1 n\n", encoding="utf-8")
    monkeypatch.setattr(extraction, "DICTIONARY_PATH", changed_dictionary)

    with pytest.raises(
        extraction.CandidateExtractionFailure,
        match="dictionary_content_mismatch",
    ):
        extraction.extract_candidates(_artifact())

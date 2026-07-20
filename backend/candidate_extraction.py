from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

import jieba


SCHEMA_VERSION = "candidate-extraction/v1"
JIEBA_VERSION = "0.42.1"
DICTIONARY_PATH = Path(__file__).parent / "data" / "jieba" / "dict.txt.big"
DICTIONARY_SHA256 = "b16011275c42955ccd81fc1adecc93a59dbb7926af69d93fc95d4943d40f6aad"


class CandidateExtractionFailure(RuntimeError):
    pass


def _validate_runtime() -> None:
    try:
        installed_version = metadata.version("jieba")
    except metadata.PackageNotFoundError as error:
        raise CandidateExtractionFailure("jieba_not_installed") from error
    if installed_version != JIEBA_VERSION:
        raise CandidateExtractionFailure("jieba_version_mismatch")
    try:
        with DICTIONARY_PATH.open("rb") as input_file:
            dictionary_hash = hashlib.file_digest(input_file, "sha256").hexdigest()
    except OSError as error:
        raise CandidateExtractionFailure("dictionary_unreadable") from error
    if dictionary_hash != DICTIONARY_SHA256:
        raise CandidateExtractionFailure("dictionary_content_mismatch")


def extract_candidates(material_blocks: Mapping[str, Any]) -> dict[str, Any]:
    if material_blocks.get("schema_version") != "material-blocks/v1":
        raise CandidateExtractionFailure("material_blocks_schema_mismatch")
    materials = material_blocks.get("materials")
    if not isinstance(materials, list):
        raise CandidateExtractionFailure("materials_invalid")

    _validate_runtime()
    tokenizer = jieba.Tokenizer(dictionary=str(DICTIONARY_PATH))
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "segmenter": {
            "distribution": "jieba",
            "version": JIEBA_VERSION,
            "dictionary": {
                "path": "data/jieba/dict.txt.big",
                "sha256": DICTIONARY_SHA256,
            },
            "settings": {"cut_all": False, "hmm": False},
        },
        "material_accounting": [],
        "block_accounting": [],
        "candidates": [],
    }
    total_blocks = 0
    processed_blocks = 0
    total_code_points = 0
    covered_code_points = 0

    for material in materials:
        if not isinstance(material, Mapping):
            raise CandidateExtractionFailure("material_invalid")
        material_id = material.get("material_id")
        if not isinstance(material_id, str) or not material_id:
            raise CandidateExtractionFailure("material_id_missing")
        if material.get("input_status") != "valid":
            result["material_accounting"].append(
                {
                    "material_id": material_id,
                    "outcome": "excluded",
                    "reason": "input_not_valid",
                }
            )
            continue
        blocks = material.get("blocks")
        if not isinstance(blocks, list):
            raise CandidateExtractionFailure("blocks_invalid")
        result["material_accounting"].append(
            {"material_id": material_id, "outcome": "processed"}
        )

        for block in blocks:
            total_blocks += 1
            if not isinstance(block, Mapping):
                raise CandidateExtractionFailure("block_invalid")
            block_id = block.get("block_id")
            if not isinstance(block_id, str) or not block_id:
                raise CandidateExtractionFailure("block_id_missing")
            if block.get("parser_status") != "success":
                result["block_accounting"].append(
                    {
                        "material_id": material_id,
                        "block_id": block_id,
                        "outcome": "excluded",
                        "reason": "block_not_successful",
                    }
                )
                continue
            text = block.get("text")
            if not isinstance(text, str):
                raise CandidateExtractionFailure("block_text_invalid")
            locator = block.get("locator")
            if not isinstance(locator, Mapping):
                raise CandidateExtractionFailure("locator_missing")
            locator = dict(locator)
            if not isinstance(locator.get("pdf_page"), int):
                raise CandidateExtractionFailure("page_locator_missing")

            try:
                pieces = list(tokenizer.cut(text, cut_all=False, HMM=False))
            except Exception as error:
                raise CandidateExtractionFailure("segmentation_failed") from error
            cursor = 0
            block_candidates = []
            for piece in pieces:
                if not isinstance(piece, str) or not piece:
                    raise CandidateExtractionFailure("empty_segment")
                end = cursor + len(piece)
                if text[cursor:end] != piece:
                    raise CandidateExtractionFailure("segmentation_reconstruction_mismatch")
                identity = (
                    f"{material_id}\0{block_id}\0{cursor}\0{end}\0{piece}\0"
                    f"{DICTIONARY_SHA256}"
                ).encode("utf-8")
                block_candidates.append(
                    {
                        "candidate_id": hashlib.sha256(identity).hexdigest(),
                        "text": piece,
                        "start": cursor,
                        "end": end,
                        "evidence_ref": {
                            "material_id": material_id,
                            "block_id": block_id,
                            "locator": locator,
                        },
                    }
                )
                cursor = end
            if cursor != len(text):
                raise CandidateExtractionFailure("segmentation_reconstruction_mismatch")

            result["candidates"].extend(block_candidates)
            result["block_accounting"].append(
                {
                    "material_id": material_id,
                    "block_id": block_id,
                    "outcome": "processed",
                    "code_points": len(text),
                    "candidate_count": len(block_candidates),
                }
            )
            processed_blocks += 1
            total_code_points += len(text)
            covered_code_points += sum(
                candidate["end"] - candidate["start"]
                for candidate in block_candidates
            )

    processed_materials = sum(
        item["outcome"] == "processed" for item in result["material_accounting"]
    )
    evidence_count = sum(bool(item["evidence_ref"]) for item in result["candidates"])
    result["coverage"] = {
        "materials": [processed_materials, len(materials)],
        "blocks": [processed_blocks, total_blocks],
        "code_points": [covered_code_points, total_code_points],
        "candidate_evidence": [evidence_count, len(result["candidates"])],
    }
    return result

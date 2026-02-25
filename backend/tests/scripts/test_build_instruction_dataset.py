import json
from pathlib import Path

from scripts.datasets.build_instruction_dataset import (
    DATASET_VERSION,
    build_instruction_records,
    load_chunks_from_jsonl,
    write_split_jsonl,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file_obj:
        for record in records:
            file_obj.write(json.dumps(record, ensure_ascii=False))
            file_obj.write("\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            payload = line.strip()
            if payload:
                rows.append(json.loads(payload))
    return rows


def _chunk(chunk_id: str, text: str, paragraph_start: int) -> dict:
    return {
        "chunk_id": chunk_id,
        "text": text,
        "meta": {
            "dataset_id": "ds-fixture",
            "source_relative_path": "backend/datasets_local/raw/fixture.txt",
            "sha256": "a" * 64,
            "created_at": "2026-02-25T00:00:00Z",
            "char_count": len(text),
            "paragraph_start": paragraph_start,
            "paragraph_end": paragraph_start,
            "title": "Fixture",
        },
    }


def test_build_instruction_dataset_from_jsonl_minimal(tmp_path) -> None:
    input_path = tmp_path / "fixture.chunks.v1.jsonl"
    _write_jsonl(
        input_path,
        [
            _chunk("ch-ds-fixture-000001", "第一段內容，這是測試資料。", 0),
            _chunk("ch-ds-fixture-000002", "第二段內容，含有額外描述。", 1),
            _chunk("ch-ds-fixture-000003", "第三段內容，用來檢查 split。", 2),
        ],
    )

    chunks, input_files = load_chunks_from_jsonl(input_path)
    assert input_files == [input_path.resolve()]

    result = build_instruction_records(
        chunks=chunks,
        split_spec="train:0.67,valid:0.33",
        seed=42,
        max_context_chars=8000,
        format_name="prompt_completion",
        schema_path=SCHEMA_PATH,
        with_dpo=False,
    )

    out_dir = tmp_path / "exports"
    output_paths = write_split_jsonl(
        out_dir=out_dir,
        records=result.sft_records,
        dataset_version=DATASET_VERSION,
    )

    built_records: list[dict] = []
    for output_path in output_paths:
        built_records.extend(_read_jsonl(output_path))

    assert len(built_records) == 3
    assert set(record["split"] for record in built_records).issubset({"train", "valid"})

    required_fields = {
        "id",
        "dataset_version",
        "split",
        "source",
        "chunk_id",
        "meta",
        "prompt",
        "completion",
        "output_json",
    }
    for record in built_records:
        assert required_fields.issubset(set(record))
        assert record["source"]["chunk_id"] == record["chunk_id"]
        assert isinstance(record["source"]["locator"], dict)
        assert record["dataset_version"] == DATASET_VERSION

    same_seed_result = build_instruction_records(
        chunks=chunks,
        split_spec="train:0.67,valid:0.33",
        seed=42,
        max_context_chars=8000,
        format_name="prompt_completion",
        schema_path=SCHEMA_PATH,
        with_dpo=False,
    )
    assert [(item["id"], item["split"]) for item in result.sft_records] == [
        (item["id"], item["split"]) for item in same_seed_result.sft_records
    ]

# backend/tests/scripts/test_validate_study_pack_dataset.py
import json
from pathlib import Path

from scripts.datasets.validate_study_pack_dataset import validate_study_pack_dataset


BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"
SAMPLES_DIR = BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "golden_samples"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False))
            file_obj.write("\n")


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            payload = line.strip()
            if payload:
                rows.append(json.loads(payload))
    return rows


def test_validate_study_pack_dataset_golden_samples_pass(tmp_path) -> None:
    samples = [
        _load_json(SAMPLES_DIR / "minimal_valid.json"),
        _load_json(SAMPLES_DIR / "typical.json"),
        _load_json(SAMPLES_DIR / "edge_case.json"),
    ]

    dataset_path = tmp_path / "golden_samples.jsonl"
    _write_jsonl(
        dataset_path,
        [
            {"id": "golden-1", "output": samples[0]},
            {"id": "golden-2", "output": samples[1]},
            {"id": "golden-3", "output": samples[2]},
        ],
    )

    result = validate_study_pack_dataset(
        input_path=dataset_path,
        schema_path=SCHEMA_PATH,
    )

    assert result.exit_code == 0
    assert result.report["total"] == 3
    assert result.report["passed"] == 3
    assert result.report["failed"] == 0
    assert result.report["errors"] == []


def test_validate_study_pack_dataset_mixed_records_report_and_quarantine(tmp_path) -> None:
    valid_output = _load_json(SAMPLES_DIR / "minimal_valid.json")
    invalid_output = dict(valid_output)
    invalid_output.pop("schema_version")

    dataset_path = tmp_path / "mixed.jsonl"
    report_path = tmp_path / "mixed.report.json"
    quarantine_path = tmp_path / "mixed.quarantine.jsonl"

    _write_jsonl(
        dataset_path,
        [
            {
                "id": "row-pass-001",
                "output": "```json\n" + json.dumps(valid_output, ensure_ascii=False) + "\n```",
            },
            {"uuid": "row-fail-001", "output": invalid_output},
        ],
    )

    result = validate_study_pack_dataset(
        input_path=dataset_path,
        schema_path=SCHEMA_PATH,
        report_path=report_path,
        quarantine_path=quarantine_path,
    )

    assert result.exit_code == 2
    assert result.report["total"] == 2
    assert result.report["passed"] == 1
    assert result.report["failed"] == 1

    report = _load_json(report_path)
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["failed"] == 1
    assert report["pass_rate"] == 0.5

    errors = report["errors"]
    assert len(errors) >= 1
    assert any(error["line_number"] == 2 for error in errors)
    assert any(error["record_id"] == "row-fail-001" for error in errors)
    assert any(error["error_path"] == "$.output" for error in errors)

    quarantine_rows = _read_jsonl(quarantine_path)
    assert len(quarantine_rows) == 1
    quarantined = quarantine_rows[0]
    assert quarantined["uuid"] == "row-fail-001"
    assert "validation_errors" in quarantined
    assert isinstance(quarantined["validation_errors"], list)
    assert len(quarantined["validation_errors"]) >= 1
    assert "error_path" in quarantined["validation_errors"][0]
    assert "message" in quarantined["validation_errors"][0]

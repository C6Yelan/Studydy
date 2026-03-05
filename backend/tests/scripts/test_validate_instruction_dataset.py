import json
from pathlib import Path

from scripts.datasets.validate_instruction_dataset import validate_instruction_dataset


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"
QUESTION_SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "question_v1" / "question_item.schema.v1.json"
TUTOR_SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "tutor_v1" / "tutor_message.schema.v1.json"
MINIMAL_VALID_PATH = (
    BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "golden_samples" / "minimal_valid.json"
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as file_obj:
        for row in rows:
            file_obj.write(json.dumps(row, ensure_ascii=False))
            file_obj.write("\n")


def _build_record(sample_id: str, output_json: dict, *, task: str) -> dict:
    return {
        "id": sample_id,
        "dataset_version": "instruction_dataset.v1",
        "split": "train",
        "task": task,
        "chunk_id": "ch-ds-fixture-000001",
        "source": {
            "doc_id": "doc-fixture-001",
            "dataset_id": "ds-fixture",
            "chunk_id": "ch-ds-fixture-000001",
            "source_relative_path": "backend/datasets_local/raw/fixture.txt",
            "sha256": "a" * 64,
            "locator": {"paragraph_start": 0, "paragraph_end": 0},
        },
        "meta": {
            "dataset_id": "ds-fixture",
            "source_relative_path": "backend/datasets_local/raw/fixture.txt",
            "sha256": "a" * 64,
            "created_at": "2026-02-25T00:00:00Z",
            "char_count": 20,
            "paragraph_start": 0,
            "paragraph_end": 0,
        },
        "prompt": "prompt",
        "completion": json.dumps(output_json, ensure_ascii=False),
        "output_json": output_json,
    }


def _question_output(*, include_evidence_refs: bool) -> dict:
    payload = {
        "question_id": "q-001",
        "concept_id": "c-001",
        "difficulty": "medium",
        "stem": "Which concept best matches this paragraph?",
        "answer_key": "A",
        "rationale": "Because the definition aligns with the target concept.",
    }
    if include_evidence_refs:
        payload["evidence_refs"] = [
            {
                "chunk_id": "ch-ds-fixture-000001",
                "locator": {
                    "source_file": "backend/datasets_local/raw/fixture.txt",
                    "paragraph_start": 0,
                    "paragraph_end": 0,
                },
            }
        ]
    return payload


def _tutor_output(*, include_evidence_refs: bool) -> dict:
    payload = {
        "summary": "學生已完成一次作答。",
        "feedback": "概念對齊不完整，需要再提示關鍵詞。",
        "next_steps": ["回顧定義", "再做一題同概念題目"],
    }
    if include_evidence_refs:
        payload["evidence_refs"] = [
            {
                "chunk_id": "ch-ds-fixture-000001",
                "locator": {
                    "source_file": "backend/datasets_local/raw/fixture.txt",
                    "paragraph_start": 0,
                    "paragraph_end": 0,
                },
            }
        ]
    return payload


def test_validate_instruction_dataset_schema_ok(tmp_path) -> None:
    output_json = json.loads(MINIMAL_VALID_PATH.read_text(encoding="utf-8"))
    dataset_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    _write_jsonl(dataset_path, [_build_record("ins-ok-001", output_json, task="domain")])

    report = validate_instruction_dataset(
        input_path=dataset_path,
        schema_path=DOMAIN_SCHEMA_PATH,
    )

    assert report["total_records"] == 1
    assert report["valid_records"] == 1
    assert report["invalid_records"] == 0
    assert report["invalid_ids"] == []


def test_validate_instruction_dataset_schema_fail_quarantine(tmp_path) -> None:
    invalid_output_json = {"schema_version": "1.0", "outline": []}
    dataset_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    quarantine_path = tmp_path / "quarantine.jsonl"
    _write_jsonl(
        dataset_path,
        [_build_record("ins-bad-001", invalid_output_json, task="domain")],
    )

    report = validate_instruction_dataset(
        input_path=dataset_path,
        schema_path=DOMAIN_SCHEMA_PATH,
        quarantine_path=quarantine_path,
    )

    assert report["total_records"] == 1
    assert report["valid_records"] == 0
    assert report["invalid_records"] == 1
    assert "ins-bad-001" in report["invalid_ids"]

    quarantine_rows = [
        json.loads(line)
        for line in quarantine_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert quarantine_rows == [
        {
            "id": "ins-bad-001",
            "file": str(dataset_path.resolve()),
            "line": 1,
        }
    ]


def test_validate_instruction_dataset_question_tutor_missing_evidence_refs(tmp_path) -> None:
    dataset_path = tmp_path / "instruction_dataset.v1.train.jsonl"
    quarantine_path = tmp_path / "quarantine.jsonl"
    _write_jsonl(
        dataset_path,
        [
            _build_record(
                "ins-question-bad-001",
                _question_output(include_evidence_refs=False),
                task="question",
            ),
            _build_record(
                "ins-tutor-bad-001",
                _tutor_output(include_evidence_refs=False),
                task="tutor",
            ),
        ],
    )

    report = validate_instruction_dataset(
        input_path=dataset_path,
        domain_schema_path=DOMAIN_SCHEMA_PATH,
        question_schema_path=QUESTION_SCHEMA_PATH,
        tutor_schema_path=TUTOR_SCHEMA_PATH,
        quarantine_path=quarantine_path,
    )

    assert report["total_records"] == 2
    assert report["valid_records"] == 0
    assert report["invalid_records"] == 2
    assert set(report["invalid_ids"]) == {"ins-question-bad-001", "ins-tutor-bad-001"}

    issue_map = {issue["id"]: issue for issue in report["issues"]}
    assert "ins-question-bad-001" in issue_map
    assert "ins-tutor-bad-001" in issue_map
    assert any("evidence_refs" in error for error in issue_map["ins-question-bad-001"]["errors"])
    assert any("evidence_refs" in error for error in issue_map["ins-tutor-bad-001"]["errors"])

    quarantine_rows = [
        json.loads(line)
        for line in quarantine_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["id"] for row in quarantine_rows} == {"ins-question-bad-001", "ins-tutor-bad-001"}

"""Validate T4 instruction dataset JSONL against required fields and task-specific schemas."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOMAIN_SCHEMA_PATH = (
    BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"
)
DEFAULT_QUESTION_SCHEMA_PATH = (
    BACKEND_ROOT / "docs" / "ai" / "question_v1" / "question_item.schema.v1.json"
)
DEFAULT_TUTOR_SCHEMA_PATH = (
    BACKEND_ROOT / "docs" / "ai" / "tutor_v1" / "tutor_message.schema.v1.json"
)
DEFAULT_INPUT_PATH = BACKEND_ROOT / "datasets_local" / "exports"
SUPPORTED_TASKS = ("domain", "question", "tutor")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def discover_dataset_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path.resolve()]
    if not input_path.exists():
        raise FileNotFoundError(f"input path does not exist: {input_path}")
    if not input_path.is_dir():
        raise ValueError(f"input path must be file or directory: {input_path}")

    preferred = sorted(path.resolve() for path in input_path.glob("instruction_dataset.v1.*.jsonl"))
    if preferred:
        return preferred

    fallback = sorted(path.resolve() for path in input_path.rglob("*.jsonl"))
    if fallback:
        return fallback
    raise ValueError(f"no JSONL files found under: {input_path}")


def validate_source(source: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(source, dict):
        return ["source must be an object"]
    for key in ("doc_id", "chunk_id", "locator"):
        if key not in source:
            errors.append(f"source missing required field: {key}")
    return errors


def validate_messages(messages: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(messages, list) or not messages:
        return ["messages must be a non-empty array"]
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            errors.append(f"messages[{index}] must be an object")
            continue
        if "role" not in message:
            errors.append(f"messages[{index}] missing role")
        if "content" not in message:
            errors.append(f"messages[{index}] missing content")
    return errors


def validate_record_shape(record: Any) -> list[str]:
    if not isinstance(record, dict):
        return ["record must be a JSON object"]

    errors: list[str] = []
    for field in ("id", "dataset_version", "split", "task", "source", "chunk_id", "meta"):
        if field not in record:
            errors.append(f"missing required field: {field}")

    task = record.get("task")
    if "task" in record:
        if not isinstance(task, str) or not task.strip():
            errors.append("task must be a non-empty string")
        elif task not in SUPPORTED_TASKS:
            errors.append(f"task must be one of {SUPPORTED_TASKS}")

    if "source" in record:
        errors.extend(validate_source(record["source"]))
        source = record.get("source")
        if isinstance(source, dict) and source.get("chunk_id") != record.get("chunk_id"):
            errors.append("source.chunk_id must equal chunk_id")

    if "meta" in record and not isinstance(record.get("meta"), dict):
        errors.append("meta must be an object")

    has_prompt_completion = isinstance(record.get("prompt"), str) and isinstance(
        record.get("completion"), str
    )
    has_messages = "messages" in record

    if not has_prompt_completion and not has_messages:
        errors.append("record must contain prompt+completion or messages")
    if has_messages:
        errors.extend(validate_messages(record.get("messages")))

    if "output_json" not in record:
        errors.append("missing required field: output_json")
    elif not isinstance(record.get("output_json"), dict):
        errors.append("output_json must be an object")

    return errors


def _load_validator(schema_path: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def validate_instruction_dataset(
    *,
    input_path: Path,
    schema_path: Path | None = None,
    domain_schema_path: Path | None = None,
    question_schema_path: Path = DEFAULT_QUESTION_SCHEMA_PATH,
    tutor_schema_path: Path = DEFAULT_TUTOR_SCHEMA_PATH,
    report_path: Path | None = None,
    quarantine_path: Path | None = None,
) -> dict[str, Any]:
    files = discover_dataset_files(input_path)
    if domain_schema_path is None:
        domain_schema_path = schema_path or DEFAULT_DOMAIN_SCHEMA_PATH

    validators: dict[str, jsonschema.Draft202012Validator] = {
        "domain": _load_validator(domain_schema_path),
        "question": _load_validator(question_schema_path),
        "tutor": _load_validator(tutor_schema_path),
    }

    issues: list[dict[str, Any]] = []
    total_records = 0

    for file_path in files:
        with file_path.open("r", encoding="utf-8") as file_obj:
            for line_number, line in enumerate(file_obj, start=1):
                payload = line.strip()
                if not payload:
                    continue
                total_records += 1

                record: Any
                parse_errors: list[str] = []
                try:
                    record = json.loads(payload)
                except json.JSONDecodeError as exc:
                    record = {}
                    parse_errors.append(f"invalid JSON: {exc}")

                record_id = (
                    str(record.get("id")).strip()
                    if isinstance(record, dict) and isinstance(record.get("id"), str)
                    else f"{file_path.name}:{line_number}"
                )
                errors = parse_errors + validate_record_shape(record)

                task_name = record.get("task") if isinstance(record, dict) else None
                output_json = record.get("output_json") if isinstance(record, dict) else None
                if isinstance(output_json, dict) and isinstance(task_name, str) and task_name in validators:
                    schema_errors = sorted(
                        validators[task_name].iter_errors(output_json), key=lambda item: list(item.path)
                    )
                    for error in schema_errors:
                        path = ".".join(str(part) for part in error.path) or "<root>"
                        errors.append(f"output_json schema violation at {path}: {error.message}")

                if errors:
                    issues.append(
                        {
                            "id": record_id,
                            "file": str(file_path),
                            "line": line_number,
                            "errors": errors,
                        }
                    )

    invalid_records = len(issues)
    valid_records = total_records - invalid_records
    invalid_ids = [issue["id"] for issue in issues]

    report = {
        "version": "v1",
        "validated_at": utc_now_iso(),
        "input_path": str(input_path),
        "schema_paths": {
            "domain": str(domain_schema_path),
            "question": str(question_schema_path),
            "tutor": str(tutor_schema_path),
        },
        "files": [str(path) for path in files],
        "total_records": total_records,
        "valid_records": valid_records,
        "invalid_records": invalid_records,
        "invalid_ids": invalid_ids,
        "issues": issues,
    }

    base_dir = input_path if input_path.is_dir() else input_path.parent
    if report_path is None:
        report_path = base_dir / "instruction_dataset.validation_report.v1.json"
    if quarantine_path is None:
        quarantine_path = base_dir / "instruction_dataset.quarantine.v1.jsonl"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    with quarantine_path.open("w", encoding="utf-8") as file_obj:
        for issue in issues:
            file_obj.write(
                json.dumps(
                    {"id": issue["id"], "file": issue["file"], "line": issue["line"]},
                    ensure_ascii=False,
                )
            )
            file_obj.write("\n")

    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate T4 instruction dataset JSONL files.")
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help=f"Instruction dataset file or directory (default: {DEFAULT_INPUT_PATH})",
    )
    parser.add_argument(
        "--study-pack-schema",
        "--domain-schema",
        dest="domain_schema",
        type=Path,
        default=DEFAULT_DOMAIN_SCHEMA_PATH,
        help=f"Domain schema path (default: {DEFAULT_DOMAIN_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--question-schema",
        type=Path,
        default=DEFAULT_QUESTION_SCHEMA_PATH,
        help=f"Question schema path (default: {DEFAULT_QUESTION_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--tutor-schema",
        type=Path,
        default=DEFAULT_TUTOR_SCHEMA_PATH,
        help=f"Tutor schema path (default: {DEFAULT_TUTOR_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Optional path for validation report JSON.",
    )
    parser.add_argument(
        "--quarantine-path",
        type=Path,
        default=None,
        help="Optional path for quarantine JSONL (invalid ids).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate_instruction_dataset(
            input_path=args.input,
            domain_schema_path=args.domain_schema,
            question_schema_path=args.question_schema,
            tutor_schema_path=args.tutor_schema,
            report_path=args.report_path,
            quarantine_path=args.quarantine_path,
        )
    except Exception as exc:
        print(f"validate_instruction_dataset failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Validation summary: "
        f"total={report['total_records']}, "
        f"valid={report['valid_records']}, "
        f"invalid={report['invalid_records']}"
    )
    if report["invalid_records"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

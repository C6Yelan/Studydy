# backend/scripts/datasets/validate_study_pack_dataset.py
"""Schema gate for Study Pack training dataset JSONL records."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = (
    BACKEND_ROOT / "docs" / "ai" / "study_pack_v1" / "study_pack.schema.v1.json"
)
RECORD_ID_KEYS = ("id", "uuid", "record_id", "sample_id")


@dataclass(slots=True)
class ValidationRunResult:
    report: dict[str, Any]
    report_path: Path
    quarantine_path: Path
    exit_code: int


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_report_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.name}.report.json")


def default_quarantine_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.name}.quarantine.jsonl")


def _is_non_empty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def extract_record_id(record: dict[str, Any]) -> str | None:
    for key in RECORD_ID_KEYS:
        value = record.get(key)
        if _is_non_empty(value):
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def build_json_path(parts: list[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
            continue
        if isinstance(part, str) and part.isidentifier():
            path += f".{part}"
            continue
        rendered = json.dumps(part, ensure_ascii=False)
        path += f"[{rendered}]"
    return path


def _unwrap_code_fence(value: str) -> str:
    text = value.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) < 2:
        return text

    first_line = lines[0].strip()
    last_line = lines[-1].strip()
    if not first_line.startswith("```") or last_line != "```":
        return text

    language = first_line[3:].strip().lower()
    if language and language != "json":
        return text

    return "\n".join(lines[1:-1]).strip()


def parse_output_as_object(output_value: Any, *, output_key: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    path = build_json_path([output_key])

    if isinstance(output_value, dict):
        return output_value, []

    if isinstance(output_value, str):
        payload = _unwrap_code_fence(output_value).strip()
        if not payload:
            return None, [{"error_path": path, "message": "output JSON string is empty"}]

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            return None, [
                {
                    "error_path": path,
                    "message": f"output is not valid JSON: {exc.msg} (char {exc.pos})",
                }
            ]

        if isinstance(parsed, str):
            nested = parsed.strip()
            if nested:
                try:
                    parsed = json.loads(nested)
                except json.JSONDecodeError:
                    pass

        if not isinstance(parsed, dict):
            return None, [{"error_path": path, "message": "output JSON must decode to an object"}]

        return parsed, []

    return None, [
        {
            "error_path": path,
            "message": f"output must be object or JSON string, got {type(output_value).__name__}",
        }
    ]


def _ensure_jsonl_file(input_path: Path) -> Path:
    resolved = input_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"input path does not exist: {input_path}")
    if not resolved.is_file():
        raise ValueError(f"input path must be a JSONL file: {input_path}")
    return resolved


def validate_study_pack_dataset(
    *,
    input_path: Path,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    output_key: str = "output",
    report_path: Path | None = None,
    quarantine_path: Path | None = None,
) -> ValidationRunResult:
    if not output_key or not output_key.strip():
        raise ValueError("output_key cannot be empty")

    resolved_input = _ensure_jsonl_file(input_path)
    resolved_schema = schema_path.resolve()

    schema = json.loads(resolved_schema.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)

    if report_path is None:
        report_path = default_report_path(resolved_input)
    if quarantine_path is None:
        quarantine_path = default_quarantine_path(resolved_input)

    errors: list[dict[str, Any]] = []
    quarantine_rows: list[dict[str, Any]] = []

    total = 0
    passed = 0
    failed = 0

    with resolved_input.open("r", encoding="utf-8") as file_obj:
        for line_number, line in enumerate(file_obj, start=1):
            payload = line.strip()
            if not payload:
                continue

            total += 1
            line_errors: list[dict[str, Any]] = []
            record: Any = None
            record_id: str | None = None

            try:
                record = json.loads(payload)
            except json.JSONDecodeError as exc:
                line_errors.append(
                    {
                        "line_number": line_number,
                        "record_id": None,
                        "error_path": "$",
                        "message": f"record is not valid JSON: {exc.msg} (char {exc.pos})",
                    }
                )

            if not line_errors:
                if not isinstance(record, dict):
                    line_errors.append(
                        {
                            "line_number": line_number,
                            "record_id": None,
                            "error_path": "$",
                            "message": f"record must be a JSON object, got {type(record).__name__}",
                        }
                    )
                else:
                    record_id = extract_record_id(record)

                    if output_key not in record:
                        line_errors.append(
                            {
                                "line_number": line_number,
                                "record_id": record_id,
                                "error_path": build_json_path([output_key]),
                                "message": f"missing output key: {output_key}",
                            }
                        )
                    else:
                        output_object, parse_errors = parse_output_as_object(
                            record[output_key],
                            output_key=output_key,
                        )

                        for parse_error in parse_errors:
                            line_errors.append(
                                {
                                    "line_number": line_number,
                                    "record_id": record_id,
                                    "error_path": parse_error["error_path"],
                                    "message": parse_error["message"],
                                }
                            )

                        if output_object is not None:
                            schema_errors = sorted(
                                validator.iter_errors(output_object),
                                key=lambda item: (list(item.path), item.message),
                            )
                            for schema_error in schema_errors:
                                line_errors.append(
                                    {
                                        "line_number": line_number,
                                        "record_id": record_id,
                                        "error_path": build_json_path(
                                            [output_key, *list(schema_error.path)]
                                        ),
                                        "message": schema_error.message,
                                    }
                                )

            if line_errors:
                failed += 1
                errors.extend(line_errors)

                validation_errors = [
                    {
                        "error_path": item["error_path"],
                        "message": item["message"],
                    }
                    for item in line_errors
                ]

                if isinstance(record, dict):
                    quarantined = dict(record)
                    quarantined["validation_errors"] = validation_errors
                elif record is None:
                    quarantined = {
                        "raw_line": payload,
                        "validation_errors": validation_errors,
                    }
                else:
                    quarantined = {
                        "raw_record": record,
                        "validation_errors": validation_errors,
                    }
                quarantine_rows.append(quarantined)
            else:
                passed += 1

    pass_rate = 0.0 if total == 0 else passed / total
    report = {
        "validated_at": utc_now_iso(),
        "schema_path": str(resolved_schema),
        "input_path": str(resolved_input),
        "output_key": output_key,
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "errors": errors,
    }

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    quarantine_path.parent.mkdir(parents=True, exist_ok=True)
    with quarantine_path.open("w", encoding="utf-8") as file_obj:
        for row in quarantine_rows:
            file_obj.write(json.dumps(row, ensure_ascii=False))
            file_obj.write("\n")

    exit_code = 0 if failed == 0 else 2
    return ValidationRunResult(
        report=report,
        report_path=report_path,
        quarantine_path=quarantine_path,
        exit_code=exit_code,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schema gate validation for Study Pack JSONL dataset")
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL path")
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Study Pack schema path (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--output-key",
        type=str,
        default="output",
        help="Record key containing schema target payload (default: output)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Validation report JSON output path",
    )
    parser.add_argument(
        "--quarantine",
        type=Path,
        default=None,
        help="Quarantine JSONL output path for failed records",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate_study_pack_dataset(
            input_path=args.input,
            schema_path=args.schema,
            output_key=args.output_key,
            report_path=args.report,
            quarantine_path=args.quarantine,
        )
    except Exception as exc:
        print(f"validate_study_pack_dataset failed: {exc}", file=sys.stderr)
        return 1

    summary = result.report
    print(
        "Schema gate summary: "
        f"total={summary['total']}, "
        f"passed={summary['passed']}, "
        f"failed={summary['failed']}, "
        f"pass_rate={summary['pass_rate']:.4f}, "
        f"report={result.report_path}, "
        f"quarantine={result.quarantine_path}"
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

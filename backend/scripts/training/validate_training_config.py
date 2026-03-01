# backend/scripts/training/validate_training_config.py
"""Validate T6 training config (YAML/JSON) with schema v1."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "training" / "training_config.schema.v1.json"


@dataclass(slots=True)
class TrainingConfigValidationResult:
    is_valid: bool
    config: dict[str, Any] | None
    config_path: Path
    schema_path: Path
    errors: list[str]


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


def load_config_document(config_path: Path) -> Any:
    resolved = config_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"config path does not exist: {config_path}")
    if not resolved.is_file():
        raise ValueError(f"config path must be a file: {config_path}")

    text = resolved.read_text(encoding="utf-8")
    suffix = resolved.suffix.lower()

    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def _load_schema(schema_path: Path) -> dict[str, Any]:
    resolved = schema_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"schema path does not exist: {schema_path}")
    schema = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError(f"schema must be a JSON object: {schema_path}")
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


def format_validation_errors(errors: list[jsonschema.ValidationError]) -> list[str]:
    formatted: list[str] = []
    for error in errors:
        error_path = build_json_path(list(error.absolute_path))
        formatted.append(f"{error_path}: {error.message}")
    return formatted


def validate_training_config_file(
    *,
    config_path: Path,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
) -> TrainingConfigValidationResult:
    resolved_config_path = config_path.resolve()
    resolved_schema_path = schema_path.resolve()

    config_data = load_config_document(resolved_config_path)
    if not isinstance(config_data, dict):
        raise ValueError("training config root must be an object")

    schema = _load_schema(resolved_schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(config_data),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    formatted_errors = format_validation_errors(errors)

    return TrainingConfigValidationResult(
        is_valid=not formatted_errors,
        config=config_data,
        config_path=resolved_config_path,
        schema_path=resolved_schema_path,
        errors=formatted_errors,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate T6 training config against schema v1.")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to training config YAML/JSON.",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Training config schema path (default: {DEFAULT_SCHEMA_PATH})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = validate_training_config_file(
            config_path=args.config,
            schema_path=args.schema,
        )
    except Exception as exc:
        print(f"validate_training_config failed: {exc}", file=sys.stderr)
        return 1

    if not result.is_valid:
        print("FAIL: training config validation failed.", file=sys.stderr)
        print(f"config: {result.config_path}", file=sys.stderr)
        print(f"schema: {result.schema_path}", file=sys.stderr)
        for error in result.errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    print("PASS: training config validation succeeded.")
    print(f"config: {result.config_path}")
    print(f"schema: {result.schema_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

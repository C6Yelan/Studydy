"""Validate dataset manifest structure and T1 gate rules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = BACKEND_ROOT / "docs" / "ai" / "datasets" / "manifest.v1.yaml"
DEFAULT_SCHEMA_PATH = BACKEND_ROOT / "docs" / "ai" / "datasets" / "manifest.schema.v1.json"

UNKNOWN_LICENSE_VALUES = {"", "unknown", "tbd"}
EMPTY_EVIDENCE_VALUES = {"", "unknown", "tbd", "n/a", "na"}


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Manifest must be a mapping object: {manifest_path}")
    return loaded


def validate_required_fields(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not manifest.get("version"):
        errors.append("Missing required field: version")

    datasets = manifest.get("datasets")
    if not isinstance(datasets, list):
        errors.append("Missing or invalid required field: datasets (must be array)")
        return errors

    required_dataset_fields = ("dataset_id", "allowed_use", "license", "privacy", "files", "updated_at")
    required_file_fields = ("relative_path", "file_type", "sha256", "size_bytes")

    for index, dataset in enumerate(datasets):
        pointer = f"datasets[{index}]"
        if not isinstance(dataset, dict):
            errors.append(f"{pointer} must be an object")
            continue

        for field in required_dataset_fields:
            if field not in dataset:
                errors.append(f"{pointer} missing required field: {field}")

        files = dataset.get("files")
        if not isinstance(files, list) or not files:
            errors.append(f"{pointer}.files must be a non-empty array")
            continue

        for file_index, file_entry in enumerate(files):
            file_pointer = f"{pointer}.files[{file_index}]"
            if not isinstance(file_entry, dict):
                errors.append(f"{file_pointer} must be an object")
                continue
            for file_field in required_file_fields:
                if file_field not in file_entry:
                    errors.append(f"{file_pointer} missing required field: {file_field}")

    return errors


def validate_against_schema(manifest: dict[str, Any], schema_path: Path) -> list[str]:
    if not schema_path.exists():
        return []

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(manifest), key=lambda err: list(err.path))

    result: list[str] = []
    for error in schema_errors:
        path = ".".join(str(part) for part in error.path) or "<root>"
        result.append(f"Schema violation at {path}: {error.message}")
    return result


def validate_train_license_rules(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    datasets = manifest.get("datasets", [])
    if not isinstance(datasets, list):
        return errors

    for index, dataset in enumerate(datasets):
        if not isinstance(dataset, dict):
            continue
        if dataset.get("allowed_use") != "train":
            continue

        pointer = f"datasets[{index}]"
        license_info = dataset.get("license")
        if not isinstance(license_info, dict):
            errors.append(f"{pointer}.license must be an object when allowed_use=train")
            continue

        license_type = str(license_info.get("type", "")).strip().lower()
        evidence = str(license_info.get("evidence", "")).strip().lower()

        if license_type in UNKNOWN_LICENSE_VALUES:
            errors.append(f"{pointer}: allowed_use=train requires concrete license.type (not unknown/TBD)")
        if evidence in EMPTY_EVIDENCE_VALUES:
            errors.append(f"{pointer}: allowed_use=train requires non-empty license.evidence")

    return errors


def validate_manifest(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    schema_path: Path = DEFAULT_SCHEMA_PATH,
    skip_schema: bool = False,
) -> list[str]:
    manifest = load_manifest(manifest_path)
    errors = []
    errors.extend(validate_required_fields(manifest))
    if not skip_schema:
        errors.extend(validate_against_schema(manifest, schema_path))
    errors.extend(validate_train_license_rules(manifest))
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate dataset manifest and T1 gate requirements.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to manifest.v1.yaml (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA_PATH,
        help=f"Path to JSON schema (default: {DEFAULT_SCHEMA_PATH})",
    )
    parser.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip JSON schema validation and run required fields + T1 rules only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_manifest(
        manifest_path=args.manifest,
        schema_path=args.schema,
        skip_schema=args.skip_schema,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"Manifest validation passed: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

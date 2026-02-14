"""Build or update dataset manifest from local raw files."""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIR = BACKEND_ROOT / "datasets_local" / "raw"
DEFAULT_MANIFEST_PATH = BACKEND_ROOT / "docs" / "ai" / "datasets" / "manifest.v1.yaml"
DEFAULT_FILE_PATH_PREFIX = "backend/datasets_local/raw"

FILE_TYPE_MAP = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "docx",
    ".ppt": "ppt",
    ".pptx": "pptx",
    ".txt": "text",
    ".md": "markdown",
    ".csv": "csv",
    ".json": "json",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def infer_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in FILE_TYPE_MAP:
        return FILE_TYPE_MAP[suffix]
    if suffix:
        return suffix.lstrip(".")
    return "binary"


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "dataset"


def dataset_id_from_relative_path(relative_path: Path) -> str:
    rel = relative_path.as_posix()
    stem = slugify(relative_path.stem)
    extension = relative_path.suffix.lstrip(".")
    ext = slugify(extension) if extension else "bin"
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
    return f"ds-{stem}-{ext}-{digest}"


def should_index_file(file_path: Path, raw_dir: Path) -> bool:
    relative_path = file_path.relative_to(raw_dir)
    return not any(part.startswith(".") for part in relative_path.parts)


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {"version": "v1", "datasets": []}
    loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Manifest must be a mapping object: {manifest_path}")
    loaded.setdefault("version", "v1")
    if not isinstance(loaded.get("datasets"), list):
        loaded["datasets"] = []
    return loaded


def build_manifest(
    raw_dir: Path = DEFAULT_RAW_DIR,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    file_path_prefix: str = DEFAULT_FILE_PATH_PREFIX,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    datasets = manifest.setdefault("datasets", [])

    existing_by_id: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        if isinstance(dataset, dict) and isinstance(dataset.get("dataset_id"), str):
            existing_by_id[dataset["dataset_id"]] = dataset

    existing_by_path: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        files = dataset.get("files")
        if not isinstance(files, list):
            continue
        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            stored_path = file_entry.get("relative_path")
            if isinstance(stored_path, str):
                existing_by_path[stored_path] = dataset

    now = utc_now_iso()
    used_ids = set(existing_by_id.keys())

    if not raw_dir.exists():
        raw_dir.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(
        path for path in raw_dir.rglob("*") if path.is_file() and should_index_file(path, raw_dir)
    )
    for file_path in raw_files:
        relative_path = file_path.relative_to(raw_dir)
        stored_relative_path = f"{file_path_prefix}/{relative_path.as_posix()}"
        file_info = {
            "relative_path": stored_relative_path,
            "file_type": infer_file_type(file_path),
            "sha256": sha256_file(file_path),
            "size_bytes": file_path.stat().st_size,
        }

        if stored_relative_path in existing_by_path:
            dataset_entry = existing_by_path[stored_relative_path]
            dataset_id = dataset_entry.get("dataset_id")
            if not isinstance(dataset_id, str) or not dataset_id:
                dataset_id = dataset_id_from_relative_path(relative_path)
                if dataset_id not in existing_by_id and dataset_id in used_ids:
                    index = 2
                    candidate = f"{dataset_id}-{index}"
                    while candidate in used_ids:
                        index += 1
                        candidate = f"{dataset_id}-{index}"
                    dataset_id = candidate
                dataset_entry["dataset_id"] = dataset_id
            used_ids.add(dataset_id)
            existing_by_id.setdefault(dataset_id, dataset_entry)
        else:
            dataset_id = dataset_id_from_relative_path(relative_path)
            if dataset_id not in existing_by_id and dataset_id in used_ids:
                index = 2
                candidate = f"{dataset_id}-{index}"
                while candidate in used_ids:
                    index += 1
                    candidate = f"{dataset_id}-{index}"
                dataset_id = candidate

            used_ids.add(dataset_id)
            dataset_entry = {
                "dataset_id": dataset_id,
                "title": relative_path.name,
                "allowed_use": "infer_only",
                "license": {
                    "type": "TBD",
                    "evidence": "",
                    "notes": "Fill before train use.",
                },
                "privacy": {
                    "pii_categories": [],
                    "redaction_status": "pending",
                    "reviewer": "",
                    "reviewed_at": None,
                    "notes": "Manual privacy review required.",
                },
                "files": [],
                "updated_at": now,
            }
            datasets.append(dataset_entry)
            existing_by_id[dataset_id] = dataset_entry
            existing_by_path[stored_relative_path] = dataset_entry

        # Keep manual fields intact; only update files + updated_at.
        dataset_entry["files"] = [file_info]
        dataset_entry["updated_at"] = now

    manifest["updated_at"] = now
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build/update dataset manifest from raw local files.")
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIR,
        help=f"Path to raw dataset directory (default: {DEFAULT_RAW_DIR})",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to manifest.v1.yaml (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--file-path-prefix",
        type=str,
        default=DEFAULT_FILE_PATH_PREFIX,
        help=f"Stored prefix for file entries (default: {DEFAULT_FILE_PATH_PREFIX})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_manifest(
        raw_dir=args.raw_dir,
        manifest_path=args.manifest,
        file_path_prefix=args.file_path_prefix,
    )
    print(f"Updated {args.manifest} with {len(manifest.get('datasets', []))} dataset entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

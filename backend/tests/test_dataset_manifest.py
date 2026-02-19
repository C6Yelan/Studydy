import hashlib
import subprocess
import sys
from pathlib import Path

import yaml


BASE = Path(__file__).resolve().parents[1]  # backend/
BUILD_SCRIPT = BASE / "scripts" / "datasets" / "build_manifest.py"
VALIDATE_SCRIPT = BASE / "scripts" / "datasets" / "validate_manifest.py"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=BASE, capture_output=True, text=True, check=False)


def test_build_manifest_generates_sha256(tmp_path):
    raw_dir = tmp_path / "datasets_local" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / ".gitkeep").write_text("", encoding="utf-8")
    test_file = raw_dir / "chapter1.txt"
    payload = b"studydy-manifest-test"
    test_file.write_bytes(payload)

    manifest_path = tmp_path / "manifest.v1.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "version": "v1",
                "datasets": [
                    {
                        "dataset_id": "ds-manual-entry",
                        "allowed_use": "train",
                        "license": {
                            "type": "CC-BY-4.0",
                            "evidence": "https://example.com/license-proof",
                            "notes": "manual license",
                        },
                        "privacy": {
                            "redaction_status": "done",
                            "reviewer": "qa-user",
                            "reviewed_at": "2026-02-14T00:00:00Z",
                            "notes": "manual privacy review",
                        },
                        "files": [
                            {
                                "relative_path": "backend/datasets_local/raw/chapter1.txt",
                                "file_type": "text",
                                "sha256": "0" * 64,
                                "size_bytes": 1,
                            }
                        ],
                        "updated_at": "2026-02-14T00:00:00Z",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--raw-dir",
            str(raw_dir),
            "--manifest",
            str(manifest_path),
            "--file-path-prefix",
            "backend/datasets_local/raw",
        ]
    )

    assert result.returncode == 0, result.stderr
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["datasets"]) == 1
    dataset = manifest["datasets"][0]
    file_entry = dataset["files"][0]
    assert dataset["dataset_id"] == "ds-manual-entry"
    assert file_entry["sha256"] == hashlib.sha256(payload).hexdigest()
    assert file_entry["size_bytes"] == len(payload)
    assert file_entry["file_type"] == "text"
    assert dataset["license"]["type"] == "CC-BY-4.0"
    assert dataset["license"]["evidence"] == "https://example.com/license-proof"
    assert dataset["privacy"]["redaction_status"] == "done"


def test_build_manifest_chinese_filenames_have_distinct_dataset_ids(tmp_path):
    raw_dir = tmp_path / "datasets_local" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    file_a = raw_dir / "第一章.txt"
    file_b = raw_dir / "第二章.txt"
    payload_a = "內容-A".encode("utf-8")
    payload_b = "內容-B".encode("utf-8")
    file_a.write_bytes(payload_a)
    file_b.write_bytes(payload_b)

    manifest_path = tmp_path / "manifest.v1.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "version": "v1",
                "datasets": [],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--raw-dir",
            str(raw_dir),
            "--manifest",
            str(manifest_path),
            "--file-path-prefix",
            "backend/datasets_local/raw",
        ]
    )

    assert result.returncode == 0, result.stderr

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    datasets = manifest["datasets"]
    assert len(datasets) == 2

    dataset_ids = [dataset["dataset_id"] for dataset in datasets]
    assert dataset_ids[0] != dataset_ids[1]

    by_path = {dataset["files"][0]["relative_path"]: dataset["files"][0] for dataset in datasets}
    entry_a = by_path["backend/datasets_local/raw/第一章.txt"]
    entry_b = by_path["backend/datasets_local/raw/第二章.txt"]
    assert entry_a["sha256"] == hashlib.sha256(payload_a).hexdigest()
    assert entry_b["sha256"] == hashlib.sha256(payload_b).hexdigest()


def test_validate_manifest_fails_on_missing_required_fields(tmp_path):
    manifest_path = tmp_path / "manifest.v1.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "version": "v1",
                "datasets": [
                    {
                        "allowed_use": "infer_only",
                        "license": {"type": "TBD", "evidence": ""},
                        "privacy": {"redaction_status": "pending"},
                        "files": [],
                        "updated_at": "2026-02-14T00:00:00Z",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--manifest",
            str(manifest_path),
            "--skip-schema",
        ]
    )

    assert result.returncode != 0
    assert "missing required field: dataset_id" in result.stderr


def test_validate_manifest_passes_for_complete_train_dataset(tmp_path):
    manifest_path = tmp_path / "manifest.v1.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "version": "v1",
                "updated_at": "2026-02-14T00:00:00Z",
                "datasets": [
                    {
                        "dataset_id": "ds-valid-train",
                        "allowed_use": "train",
                        "license": {
                            "type": "CC-BY-4.0",
                            "evidence": "https://creativecommons.org/licenses/by/4.0/",
                        },
                        "privacy": {"redaction_status": "done"},
                        "files": [
                            {
                                "relative_path": "backend/datasets_local/raw/demo.md",
                                "file_type": "markdown",
                                "sha256": "a" * 64,
                                "size_bytes": 42,
                            }
                        ],
                        "updated_at": "2026-02-14T00:00:00Z",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            str(VALIDATE_SCRIPT),
            "--manifest",
            str(manifest_path),
            "--skip-schema",
        ]
    )

    assert result.returncode == 0, result.stderr

from __future__ import annotations

import json
from pathlib import Path

import pytest

import material_runtime_files
from material_runtime_files import (
    canonical_json_bytes,
    publish_runtime_json,
)


def test_canonical_json_bytes_are_utf8_deterministic() -> None:
    value = {"z": "教材", "a": [2, 1]}

    first = canonical_json_bytes(value)
    second = canonical_json_bytes(value)

    assert first == second == '{"a":[2,1],"z":"教材"}\n'.encode()
    assert json.loads(first) == value


def test_publish_uses_adjacent_temporary_file_and_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable = ".studydy-runtime/domain/stable/value.json"
    value = {"schema_version": "example/v1", "items": [2, 1]}
    stable_parent = (tmp_path / stable).parent
    temporary_directories: list[Path] = []
    real_mkstemp = material_runtime_files.tempfile.mkstemp

    def record_temporary_directory(*args, **kwargs):
        temporary_directories.append(Path(kwargs["dir"]))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(
        material_runtime_files.tempfile,
        "mkstemp",
        record_temporary_directory,
    )

    publish_runtime_json(
        value,
        repo_root=tmp_path,
        stable_path=stable,
    )
    publish_runtime_json(
        value,
        repo_root=tmp_path,
        stable_path=stable,
    )

    stable_path = tmp_path / stable
    assert stable_path.read_bytes() == canonical_json_bytes(value)
    # 暫存檔必須與 stable 位於同一目錄，才能可靠地進行原子替換。
    assert temporary_directories == [stable_parent, stable_parent]
    assert not list((tmp_path / ".studydy-runtime").rglob("*.tmp"))


def test_atomic_failure_preserves_prior_stable_and_cleans_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stable = ".studydy-runtime/domain/stable/value.json"
    stable_path = tmp_path / stable
    stable_path.parent.mkdir(parents=True)
    stable_path.write_bytes(b"prior-stable")
    real_replace = material_runtime_files.os.replace

    def fail_stable_replace(source: Path, destination: Path) -> None:
        if Path(destination) == stable_path:
            raise OSError("synthetic promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(material_runtime_files.os, "replace", fail_stable_replace)

    with pytest.raises(OSError, match="synthetic promotion failure"):
        publish_runtime_json(
            {"new": True},
            repo_root=tmp_path,
            stable_path=stable,
        )

    assert stable_path.read_bytes() == b"prior-stable"
    assert not list((tmp_path / ".studydy-runtime").rglob("*.tmp"))

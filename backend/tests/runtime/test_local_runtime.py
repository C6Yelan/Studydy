import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

import runtime.local_runtime as local_runtime
from runtime.local_app import read_local_ai_config_from_environment
from runtime.material_processing import MaterialProcessingError


def _mirrored_config(tmp_path: Path, contents: bytes = b"installed") -> dict:
    config = read_local_ai_config_from_environment(
        {"STUDYDY_LOCAL_RUNTIME_ROOT": str(tmp_path / "local-runtime")}
    )
    package_root = Path(config["site_packages"]) / "studydy_local_ai"
    package_root.mkdir(parents=True)
    for name in local_runtime._SOURCE_NAMES:
        target = package_root / name
        target.write_bytes(contents + name.encode())
        target.chmod(0o640)
    return config


def _installed_bytes(config: dict) -> dict[str, bytes]:
    package_root = Path(config["site_packages"]) / "studydy_local_ai"
    return {
        name: (package_root / name).read_bytes()
        for name in local_runtime._SOURCE_NAMES
    }


def _tracked_bytes() -> dict[str, bytes]:
    source_root = Path(__file__).parents[3] / "local_ai/src/studydy_local_ai"
    return {
        name: (source_root / name).read_bytes()
        for name in local_runtime._SOURCE_NAMES
    }


def test_sync_is_idempotent_and_explicit_rollback_restores_complete_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    original = _installed_bytes(config)
    monkeypatch.setattr(
        local_runtime,
        "validate_installed_local_runtime",
        lambda _: ({"schema": "binding"}, 20),
    )

    synchronized = local_runtime.sync_local_runtime(config)

    assert synchronized == {
        "status": "succeeded",
        "command": "sync",
        "files_total": 3,
        "files_updated": 3,
    }
    assert _installed_bytes(config) == _tracked_bytes()
    package_root = Path(config["site_packages"]) / "studydy_local_ai"
    backup_root = package_root.parent / local_runtime._BACKUP_NAME
    assert {path.name for path in backup_root.iterdir()} == set(
        local_runtime._SOURCE_NAMES
    )
    assert {
        name: (backup_root / name).read_bytes()
        for name in local_runtime._SOURCE_NAMES
    } == original
    assert all(
        stat.S_IMODE((backup_root / name).stat().st_mode) == 0o640
        for name in local_runtime._SOURCE_NAMES
    )

    assert local_runtime.sync_local_runtime(config)["files_updated"] == 0
    assert local_runtime.rollback_local_runtime(config) == {
        "status": "succeeded",
        "command": "rollback",
        "files_total": 3,
        "files_restored": 3,
    }
    assert _installed_bytes(config) == original
    assert backup_root.is_dir()


def test_sync_rolls_back_attempted_targets_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    original = _installed_bytes(config)
    monkeypatch.setattr(
        local_runtime,
        "validate_installed_local_runtime",
        lambda _: ({"schema": "binding"}, 20),
    )
    real_replace = local_runtime._atomic_replace
    calls = 0

    def fail_second(source, target, mode):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise MaterialProcessingError(
                "MATERIAL_CONFIGURATION_INVALID",
                component="transaction",
                reason="LOCAL_RUNTIME_WRITE_FAILED",
            )
        real_replace(source, target, mode)

    monkeypatch.setattr(local_runtime, "_atomic_replace", fail_second)

    with pytest.raises(MaterialProcessingError) as failure:
        local_runtime.sync_local_runtime(config)

    assert failure.value.reason == "LOCAL_RUNTIME_WRITE_FAILED"
    assert _installed_bytes(config) == original


def test_sync_rolls_back_when_shared_post_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    original = _installed_bytes(config)
    monkeypatch.setattr(
        local_runtime,
        "validate_installed_local_runtime",
        lambda _: (_ for _ in ()).throw(
            MaterialProcessingError(
                "MATERIAL_CONFIGURATION_INVALID",
                component="concept_model",
                reason="LOCAL_RUNTIME_HASH_MISMATCH",
            )
        ),
    )

    with pytest.raises(MaterialProcessingError) as failure:
        local_runtime.sync_local_runtime(config)

    assert failure.value.component == "concept_model"
    assert failure.value.reason == "LOCAL_RUNTIME_HASH_MISMATCH"
    assert _installed_bytes(config) == original


def test_sync_rejects_unsafe_target_and_conflicting_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    monkeypatch.setattr(
        local_runtime,
        "validate_installed_local_runtime",
        lambda _: ({"schema": "binding"}, 20),
    )
    package_root = Path(config["site_packages"]) / "studydy_local_ai"
    unsafe = package_root / "protocol.py"
    unsafe.unlink()
    unsafe.symlink_to(package_root / "__init__.py")

    with pytest.raises(MaterialProcessingError) as unsafe_failure:
        local_runtime.sync_local_runtime(config)

    assert unsafe_failure.value.component == "ocr_package"
    assert unsafe_failure.value.reason == "LOCAL_RUNTIME_UNSAFE_TARGET"

    unsafe.unlink()
    unsafe.write_bytes(b"installed protocol")
    (package_root.parent / local_runtime._BACKUP_NAME).mkdir()
    with pytest.raises(MaterialProcessingError) as backup_failure:
        local_runtime.sync_local_runtime(config)
    assert backup_failure.value.component == "backup"
    assert backup_failure.value.reason == "LOCAL_RUNTIME_BACKUP_CONFLICT"


@pytest.mark.parametrize("target_kind", ["missing", "directory"])
def test_sync_rejects_missing_and_nonregular_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
):
    config = _mirrored_config(tmp_path)
    target = Path(config["site_packages"]) / "studydy_local_ai/protocol.py"
    target.unlink()
    if target_kind == "directory":
        target.mkdir()
    monkeypatch.setattr(
        local_runtime,
        "validate_installed_local_runtime",
        lambda _: ({"schema": "binding"}, 20),
    )

    with pytest.raises(MaterialProcessingError) as failure:
        local_runtime.sync_local_runtime(config)

    assert failure.value.reason == (
        "LOCAL_RUNTIME_MISSING"
        if target_kind == "missing"
        else "LOCAL_RUNTIME_UNSAFE_TARGET"
    )


def test_sync_rejects_wrong_owner_and_cross_filesystem_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    real_getuid = local_runtime.os.getuid
    monkeypatch.setattr(local_runtime.os, "getuid", lambda: real_getuid() + 1)
    with pytest.raises(MaterialProcessingError) as owner_failure:
        local_runtime.sync_local_runtime(config)
    assert owner_failure.value.reason == "LOCAL_RUNTIME_UNSAFE_TARGET"

    monkeypatch.setattr(local_runtime.os, "getuid", real_getuid)
    real_regular_file = local_runtime._owned_regular_file

    def other_device(path, *, component):
        path_status = real_regular_file(path, component=component)
        if component == "ocr_package" and path.name == "protocol.py":
            return SimpleNamespace(
                st_mode=path_status.st_mode,
                st_uid=path_status.st_uid,
                st_dev=path_status.st_dev + 1,
            )
        return path_status

    monkeypatch.setattr(local_runtime, "_owned_regular_file", other_device)
    with pytest.raises(MaterialProcessingError) as device_failure:
        local_runtime.sync_local_runtime(config)
    assert device_failure.value.component == "backup"
    assert device_failure.value.reason == "LOCAL_RUNTIME_UNSAFE_TARGET"


def test_atomic_replace_fsyncs_file_then_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    observed = []
    real_replace = local_runtime.os.replace
    real_fsync_directory = local_runtime._fsync_directory

    def replace(first, second):
        observed.append("replace")
        real_replace(first, second)

    def fsync_directory(path):
        observed.append("fsync_parent")
        real_fsync_directory(path)

    monkeypatch.setattr(local_runtime.os, "replace", replace)
    monkeypatch.setattr(local_runtime, "_fsync_directory", fsync_directory)

    local_runtime._atomic_replace(source, target, 0o640)

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert observed == ["replace", "fsync_parent"]


def test_source_mismatch_stops_before_target_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    original = _installed_bytes(config)
    config["runtime_lock"]["ocr"]["package_sources"]["protocol.py"] = "0" * 64
    monkeypatch.setattr(local_runtime, "formal_runtime_binding", lambda _: {})

    with pytest.raises(MaterialProcessingError) as failure:
        local_runtime.sync_local_runtime(config)

    assert failure.value.component == "product_code"
    assert failure.value.reason == "LOCAL_RUNTIME_HASH_MISMATCH"
    assert _installed_bytes(config) == original


def test_verify_calls_shared_validator_without_filesystem_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _mirrored_config(tmp_path)
    observed = []
    monkeypatch.setattr(
        local_runtime,
        "validate_installed_local_runtime",
        lambda value: observed.append(value) or ({"schema": "binding"}, 20),
    )
    monkeypatch.setattr(
        local_runtime.os,
        "replace",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not replace")),
    )
    monkeypatch.setattr(
        local_runtime.tempfile,
        "mkdtemp",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not create")
        ),
    )

    assert local_runtime.verify_local_runtime(config) == {
        "status": "succeeded",
        "command": "verify",
        "verified_files": 20,
        "expected_files": 20,
    }
    assert observed == [config]


def test_cli_failure_is_fixed_safe_json(capsys, monkeypatch):
    monkeypatch.setattr(
        local_runtime,
        "read_local_ai_config_from_environment",
        lambda _: {},
    )
    monkeypatch.setattr(
        local_runtime,
        "verify_local_runtime",
        lambda _: (_ for _ in ()).throw(
            MaterialProcessingError(
                "private/path/hash",
                component="ocr_model",
                reason="LOCAL_RUNTIME_HASH_MISMATCH",
            )
        ),
    )

    assert local_runtime.main(["verify"], {}) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure == {
        "status": "failed",
        "command": "verify",
        "component": "ocr_model",
        "reason": "LOCAL_RUNTIME_HASH_MISMATCH",
        "files_total": 3,
        "verified_files": 0,
        "expected_files": 20,
    }
    assert "path" not in json.dumps(failure)


def test_cli_rejects_arbitrary_arguments_without_echo(capsys):
    assert local_runtime.main(["sync", "/private/target"], {}) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["component"] == "layout"
    assert failure["reason"] == "LOCAL_RUNTIME_SETTINGS_MISMATCH"
    assert "/private/target" not in json.dumps(failure)

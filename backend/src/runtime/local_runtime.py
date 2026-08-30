from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping

from learning_adaptation.assessment_runtime import (
    assessment_runtime_binding,
    load_assessment_runtime_lock,
)

from .local_app import read_local_ai_config_from_environment
from .material_processing import (
    MaterialProcessingError,
    _runtime_error,
    formal_runtime_binding,
    validate_installed_local_runtime,
)


_MATERIAL_SOURCE_NAMES = (
    "__init__.py",
    "protocol.py",
    "ocr_process.py",
    "relation_process.py",
)
_EQUIVALENCE_SOURCE_NAMES = ("equivalence_process.py",)
_ASSESSMENT_SOURCE_NAMES = (
    "assessment_process.py",
)
_SOURCE_NAMES = (
    _MATERIAL_SOURCE_NAMES
    + _EQUIVALENCE_SOURCE_NAMES
    + _ASSESSMENT_SOURCE_NAMES
)
_EXPECTED_RUNTIME_FILES = 30
_BACKUP_NAME = ".studydy_local_ai-backup"
_CHUNK = 1024 * 1024


def _hash_file(path: Path, *, component: str = "product_code") -> str:
    digest = sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(_CHUNK):
                digest.update(chunk)
    except OSError:
        raise _runtime_error(component, "LOCAL_RUNTIME_MISSING") from None
    return digest.hexdigest()


def _source_and_target_files(
    local_config: dict[str, Any],
) -> tuple[tuple[Path, Path], ...]:
    try:
        material_sources = local_config["runtime_lock"]["ocr"][
            "package_sources"
        ]
        equivalence_source = local_config["runtime_lock"][
            "concept_equivalence"
        ]["package_source"]
        assessment_sources = load_assessment_runtime_lock()["package_sources"]
    except (KeyError, TypeError):
        raise _runtime_error("runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH") from None
    if (
        not isinstance(material_sources, dict)
        or tuple(material_sources) != _MATERIAL_SOURCE_NAMES
        or not isinstance(equivalence_source, dict)
        or equivalence_source.get("name") != _EQUIVALENCE_SOURCE_NAMES[0]
        or set(equivalence_source) != {"name", "sha256"}
        or not isinstance(assessment_sources, dict)
        or tuple(assessment_sources) != _ASSESSMENT_SOURCE_NAMES
    ):
        raise _runtime_error("runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH")
    package_sources = {
        **material_sources,
        equivalence_source["name"]: equivalence_source["sha256"],
        **assessment_sources,
    }
    repository_root = Path(__file__).resolve().parents[3]
    source_root = repository_root / "local_ai" / "src" / "studydy_local_ai"
    target_root = Path(local_config["site_packages"]) / "studydy_local_ai"
    pairs = tuple(
        (source_root / name, target_root / name) for name in _SOURCE_NAMES
    )
    for name, (source, _) in zip(_SOURCE_NAMES, pairs, strict=True):
        try:
            source_status = source.lstat()
        except OSError:
            raise _runtime_error("product_code", "LOCAL_RUNTIME_MISSING") from None
        if stat.S_ISLNK(source_status.st_mode) or not stat.S_ISREG(
            source_status.st_mode
        ):
            raise _runtime_error(
                "product_code", "LOCAL_RUNTIME_UNSAFE_TARGET"
            )
        if _hash_file(source) != package_sources[name]:
            raise _runtime_error(
                "product_code", "LOCAL_RUNTIME_HASH_MISMATCH"
            )
    return pairs


def _owned_directory(path: Path, *, component: str) -> os.stat_result:
    try:
        path_status = path.lstat()
    except FileNotFoundError:
        raise _runtime_error(component, "LOCAL_RUNTIME_MISSING") from None
    except OSError:
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET") from None
    if (
        stat.S_ISLNK(path_status.st_mode)
        or not stat.S_ISDIR(path_status.st_mode)
        or path_status.st_uid != os.getuid()
    ):
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET")
    return path_status


def _owned_regular_file(path: Path, *, component: str) -> os.stat_result:
    try:
        path_status = path.lstat()
    except FileNotFoundError:
        raise _runtime_error(component, "LOCAL_RUNTIME_MISSING") from None
    except OSError:
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET") from None
    if (
        stat.S_ISLNK(path_status.st_mode)
        or not stat.S_ISREG(path_status.st_mode)
        or path_status.st_uid != os.getuid()
    ):
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET")
    return path_status


def _safe_targets(
    pairs: tuple[tuple[Path, Path], ...],
) -> tuple[tuple[Path, os.stat_result], ...]:
    target_root = pairs[0][1].parent
    root_status = _owned_directory(target_root, component="ocr_package")
    parent_status = _owned_directory(target_root.parent, component="backup")
    targets = []
    for _, target in pairs:
        target_status = _owned_regular_file(
            target, component="ocr_package"
        )
        targets.append((target, target_status))
    if parent_status.st_dev != root_status.st_dev or any(
        target_status.st_dev != root_status.st_dev
        for _, target_status in targets
    ):
        raise _runtime_error("backup", "LOCAL_RUNTIME_UNSAFE_TARGET")
    return tuple(targets)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_new_file(source: Path, destination: Path, mode: int) -> None:
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        with source.open("rb") as source_file, os.fdopen(
            descriptor, "wb", closefd=False
        ) as destination_file:
            while chunk := source_file.read(_CHUNK):
                destination_file.write(chunk)
            destination_file.flush()
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_temporary_backup(path: Path) -> None:
    try:
        for name in _SOURCE_NAMES:
            (path / name).unlink(missing_ok=True)
        path.rmdir()
    except OSError:
        pass


def _build_backup(
    targets: tuple[tuple[Path, os.stat_result], ...],
) -> Path:
    target_root = targets[0][0].parent
    backup_root = target_root.parent / _BACKUP_NAME
    if os.path.lexists(backup_root):
        raise _runtime_error("backup", "LOCAL_RUNTIME_BACKUP_CONFLICT")
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f"{_BACKUP_NAME}-", dir=target_root.parent)
    )
    try:
        for target, target_status in targets:
            backup_file = temporary_root / target.name
            _copy_new_file(target, backup_file, stat.S_IMODE(target_status.st_mode))
            if _hash_file(backup_file, component="backup") != _hash_file(
                target, component="ocr_package"
            ):
                raise _runtime_error("backup", "LOCAL_RUNTIME_WRITE_FAILED")
        _fsync_directory(temporary_root)
        os.rename(temporary_root, backup_root)
        _fsync_directory(backup_root.parent)
    except MaterialProcessingError:
        _remove_temporary_backup(temporary_root)
        raise
    except OSError:
        _remove_temporary_backup(temporary_root)
        raise _runtime_error("backup", "LOCAL_RUNTIME_WRITE_FAILED") from None
    return backup_root


def _validated_backup(
    backup_root: Path, device: int
) -> tuple[Path, ...]:
    backup_status = _owned_directory(backup_root, component="backup")
    if backup_status.st_dev != device:
        raise _runtime_error("backup", "LOCAL_RUNTIME_BACKUP_CONFLICT")
    try:
        names = {entry.name for entry in backup_root.iterdir()}
    except OSError:
        raise _runtime_error("backup", "LOCAL_RUNTIME_BACKUP_CONFLICT") from None
    if names != set(_SOURCE_NAMES):
        raise _runtime_error("backup", "LOCAL_RUNTIME_BACKUP_CONFLICT")
    backup_files = tuple(backup_root / name for name in _SOURCE_NAMES)
    for backup_file in backup_files:
        backup_file_status = _owned_regular_file(backup_file, component="backup")
        if backup_file_status.st_dev != device:
            raise _runtime_error("backup", "LOCAL_RUNTIME_BACKUP_CONFLICT")
    return backup_files


def _atomic_replace(source: Path, target: Path, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}-", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as source_file, os.fdopen(descriptor, "wb") as output:
            while chunk := source_file.read(_CHUNK):
                output.write(chunk)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise _runtime_error("transaction", "LOCAL_RUNTIME_WRITE_FAILED") from None


def _restore_targets(
    targets: tuple[tuple[Path, os.stat_result], ...],
    backup_root: Path,
    names: set[str] | None = None,
) -> int:
    device = _owned_directory(targets[0][0].parent, component="backup").st_dev
    backup_files = _validated_backup(backup_root, device)
    restored = 0
    for (target, _), backup_file in zip(targets, backup_files, strict=True):
        if names is not None and target.name not in names:
            continue
        backup_status = _owned_regular_file(backup_file, component="backup")
        _atomic_replace(
            backup_file, target, stat.S_IMODE(backup_status.st_mode)
        )
        restored += 1
    return restored


def verify_local_runtime(local_config: dict[str, Any]) -> dict[str, Any]:
    verified_files = _verified_runtime_files(local_config)
    if verified_files != _EXPECTED_RUNTIME_FILES:
        raise _runtime_error("runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH")
    return {
        "status": "succeeded",
        "command": "verify",
        "verified_files": verified_files,
        "expected_files": _EXPECTED_RUNTIME_FILES,
    }


def _verified_runtime_files(local_config: dict[str, Any]) -> int:
    _, material_files = validate_installed_local_runtime(local_config)
    assessment_runtime_binding(local_config, load_assessment_runtime_lock())
    return material_files + len(_ASSESSMENT_SOURCE_NAMES)


def sync_local_runtime(local_config: dict[str, Any]) -> dict[str, Any]:
    formal_runtime_binding(local_config)
    pairs = _source_and_target_files(local_config)
    targets = _safe_targets(pairs)
    changed = tuple(
        (source, target, target_status)
        for (source, target), (_, target_status) in zip(pairs, targets, strict=True)
        if _hash_file(source) != _hash_file(target, component="ocr_package")
    )
    if not changed:
        return {
            "status": "succeeded",
            "command": "sync",
            "files_total": len(pairs),
            "files_updated": 0,
        }
    backup_root = _build_backup(targets)
    attempted: set[str] = set()
    try:
        for source, target, target_status in changed:
            attempted.add(target.name)
            _atomic_replace(source, target, stat.S_IMODE(target_status.st_mode))
        verified_files = _verified_runtime_files(local_config)
        if verified_files != _EXPECTED_RUNTIME_FILES:
            raise _runtime_error("runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH")
    except Exception as error:
        try:
            if attempted:
                _restore_targets(targets, backup_root, attempted)
        except Exception:
            raise _runtime_error(
                "transaction", "LOCAL_RUNTIME_WRITE_FAILED"
            ) from None
        if isinstance(error, MaterialProcessingError):
            raise error
        raise _runtime_error("transaction", "LOCAL_RUNTIME_WRITE_FAILED") from None
    return {
        "status": "succeeded",
        "command": "sync",
        "files_total": len(pairs),
        "files_updated": len(changed),
    }


def rollback_local_runtime(local_config: dict[str, Any]) -> dict[str, Any]:
    formal_runtime_binding(local_config)
    pairs = _source_and_target_files(local_config)
    targets = _safe_targets(pairs)
    backup_root = targets[0][0].parent.parent / _BACKUP_NAME
    try:
        restored = _restore_targets(targets, backup_root)
    except MaterialProcessingError:
        raise
    except Exception:
        raise _runtime_error("transaction", "LOCAL_RUNTIME_WRITE_FAILED") from None
    return {
        "status": "succeeded",
        "command": "rollback",
        "files_total": len(pairs),
        "files_restored": restored,
    }


def _failure(command: str, error: Exception) -> dict[str, Any]:
    component = getattr(error, "component", None)
    reason = getattr(error, "reason", None)
    failure = {
        "status": "failed",
        "command": command,
        "component": component if component is not None else "layout",
        "reason": (
            reason
            if reason is not None
            else "LOCAL_RUNTIME_SETTINGS_MISMATCH"
        ),
        "files_total": len(_SOURCE_NAMES),
    }
    if command == "verify":
        failure["verified_files"] = 0
        failure["expected_files"] = _EXPECTED_RUNTIME_FILES
    elif command == "rollback":
        failure["files_restored"] = 0
    else:
        failure["files_updated"] = 0
    return failure


def main(
    argv: list[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = (
        "verify"
        if arguments == ["verify"]
        else "rollback"
        if arguments == ["sync", "--rollback"]
        else "sync"
    )
    try:
        if arguments not in (["verify"], ["sync"], ["sync", "--rollback"]):
            raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
        local_config = read_local_ai_config_from_environment(
            os.environ if environment is None else environment
        )
        if command == "verify":
            response = verify_local_runtime(local_config)
        elif command == "rollback":
            response = rollback_local_runtime(local_config)
        else:
            response = sync_local_runtime(local_config)
        print(json.dumps(response, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(_failure(command, error), sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

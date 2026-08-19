from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from pdf_evidence.concept_api import (
    CONCEPT_SERVER_READY_TIMEOUT_SECONDS,
    ConceptAPIError,
    chat_completions_url,
)
from pdf_evidence.text_first_bundle import read_producer_bundle
from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.text_first_run import (
    _validate_runtime_lock,
    run_full_text_first_pdf,
)

from .storage.artifacts import open_verified_source_pdf
from .storage.material_review_outputs import MaterialRunOutputError, publish_material_outputs
from .storage.tables import (
    Learner,
    MaterialProcessingRun as MaterialProcessingRunRow,
    database_session,
)


_CHUNK = 1024 * 1024
_CONFIG_KEYS = {
    "private_runtime_root",
    "runtime_lock",
    "python_executable",
    "site_packages",
    "ocr_model_root",
    "concept_api_base_url",
    "concept_model",
    "concept_server_executable",
    "concept_model_root",
    "concept_kv_cache_bytes",
    "concept_max_concurrency",
    "concept_max_model_len",
}
_CONFIG_PATH_KEYS = {
    "private_runtime_root",
    "python_executable",
    "site_packages",
    "ocr_model_root",
    "concept_server_executable",
    "concept_model_root",
}
_LOCKED_FILES = {
    "local_ai/runtime-lock.json": "ef47c486e1680c49d6060b5aadc17a8ec23af807d091ad4a77c6413dd53c366e",
    "backend/src/pdf_evidence/ocr_page_evidence.py": "464dd905c89675ec57775e0d6170416f4702f18407d7e06dce95d054d7769f03",
    "backend/src/pdf_evidence/concept_generation.py": "1a3ba77a2aca9238b41e0d82079792a0d51067f04bd27c49f1f07a89ba17bce1",
    "backend/src/pdf_evidence/concept_api.py": "ecfc16da63825d093c0d1b269b8cba41de203dda105730d6ce8699606d8df609",
    "backend/src/pdf_evidence/local_ai_process.py": "5a4396631eb82426ae60d809a63d5245ff88777d762a5365ace98e602f25182b",
}
_BINDING_FILES = (
    "backend/src/pdf_evidence/artifact_reason_codes.py",
    "backend/src/pdf_evidence/concept_evidence_output.py",
    "backend/src/pdf_evidence/concept_api.py",
    "backend/src/pdf_evidence/text_first_bundle.py",
    "backend/src/pdf_evidence/text_first_run.py",
    "backend/src/pdf_evidence/study_material_output.py",
    "backend/src/knowledge_map/artifacts.py",
    "backend/src/runtime/material_processing.py",
    "backend/src/runtime/local_app.py",
    "backend/src/pdf_evidence/source_pdf.py",
    "backend/src/runtime/storage/material_review_outputs.py",
)
_LOCAL_AI_SOURCE_HASHES = {
    "__init__.py": "c7a3ebd9b5d9dcd05a9c8a0610efb0ee5481d4733dd4101872bcf72c5ee4008c",
    "protocol.py": "2cf8c64d90ea79f76606e22caaf465f16ffd4153adbe83c90c18e6aa51bead43",
    "ocr_process.py": "d6f431c990630b60311ef0e9737ea4805896eb709eb69dd24644d93a580a232a",
}
_PACKAGE_VERSIONS = {
    "studydy-local-ai": "0.1.0",
    "setuptools": "84.0.0",
    "torch": "2.10.0+cu128",
    "torchvision": "0.25.0+cu128",
    "transformers": "4.57.1",
}


class MaterialProcessingError(RuntimeError):
    """Material processing 失敗且不揭露教材、設定或資料庫細節。"""


@dataclass(frozen=True)
class MaterialProcessingRun:
    run_id: UUID
    learner_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    runtime_binding: dict[str, Any] = field(repr=False)
    status: str
    error_code: str | None
    output_binding: dict[str, Any] | None = field(repr=False)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class ClaimedMaterialProcessingRun:
    run: MaterialProcessingRun = field(repr=False)


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical(value: Any) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID") from None
    return sha256(encoded).digest()


def _key_digest(value: Any) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except (AttributeError, UnicodeError):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID") from None
    if not 1 <= len(encoded) <= 256:
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    return sha256(encoded).digest()


def _row(row: MaterialProcessingRunRow) -> MaterialProcessingRun:
    return MaterialProcessingRun(
        run_id=row.run_id,
        learner_id=row.learner_id,
        material_id=row.material_id,
        source_artifact_id=row.source_artifact_id,
        runtime_binding=deepcopy(row.runtime_binding),
        status=row.status,
        error_code=row.error_code,
        output_binding=deepcopy(row.output_binding),
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _file_sha256(path: Path) -> str:
    try:
        with path.open("rb") as source:
            digest = sha256()
            while chunk := source.read(_CHUNK):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None


@dataclass(frozen=True)
class _RuntimeFile:
    path: Path
    expected_sha256: str
    expected_size: int | None = None


def _absolute_runtime_path(value: str, *, is_directory: bool) -> Path:
    """只接受已存在的絕對本機路徑，避免 runtime root 被換成連結。"""

    path = Path(value)
    try:
        path_status = path.lstat()
    except OSError:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None
    expected_kind = stat.S_ISDIR if is_directory else stat.S_ISREG
    if not path.is_absolute() or stat.S_ISLNK(path_status.st_mode) or not expected_kind(
        path_status.st_mode
    ):
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    return path


def _prepare_private_runtime_root(value: str) -> None:
    """建立 owner-only runtime root；既有寬鬆或連結目錄一律拒絕。"""

    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path_status = path.lstat()
    except OSError:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None
    if (
        not stat.S_ISDIR(path_status.st_mode)
        or stat.S_ISLNK(path_status.st_mode)
        or path_status.st_uid != os.getuid()
        or stat.S_IMODE(path_status.st_mode) & 0o077
    ):
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")


def _runtime_files(local_config: dict[str, Any]) -> tuple[_RuntimeFile, ...]:
    """把 runtime lock 對應到 OCR 與 Qwen 真正會開啟的本機檔案。"""

    runtime_lock = local_config["runtime_lock"]
    python_executable = _absolute_runtime_path(
        local_config["python_executable"], is_directory=False
    )
    if not os.access(python_executable, os.X_OK):
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    site_packages = _absolute_runtime_path(
        local_config["site_packages"], is_directory=True
    )
    ocr_model_root = _absolute_runtime_path(
        local_config["ocr_model_root"], is_directory=True
    )
    concept_server_executable = _absolute_runtime_path(
        local_config["concept_server_executable"], is_directory=False
    )
    if not os.access(concept_server_executable, os.X_OK):
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    concept_model_root = _absolute_runtime_path(
        local_config["concept_model_root"], is_directory=True
    )
    package_root = site_packages / "studydy_local_ai"
    files = [
        _RuntimeFile(
            python_executable,
            runtime_lock["python"]["executable_sha256"],
        ),
        *(
            _RuntimeFile(package_root / name, expected_sha256)
            for name, expected_sha256 in _LOCAL_AI_SOURCE_HASHES.items()
        ),
        _RuntimeFile(
            ocr_model_root / "config.json",
            runtime_lock["ocr"]["config_sha256"],
        ),
        *(
            _RuntimeFile(ocr_model_root / name, expected_sha256)
            for name, expected_sha256 in runtime_lock["ocr"]["reviewed_code"].items()
        ),
    ]
    for required_file in runtime_lock["ocr"]["required_files"]:
        name = required_file["name"]
        if Path(name).name != name:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        files.append(
            _RuntimeFile(
                ocr_model_root / name,
                required_file["sha256"],
                required_file["size"],
            )
        )
    for required_file in runtime_lock["semantic"]["required_files"]:
        name = required_file["name"]
        if Path(name).name != name:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        files.append(
            _RuntimeFile(
                concept_model_root / name,
                required_file["sha256"],
                required_file["size"],
            )
        )
    return tuple(files)


def _distribution_versions(site_packages: Path) -> dict[str, str]:
    """直接讀取固定 site-packages metadata，不執行待驗 runtime 程式。"""

    found: dict[str, str] = {}
    try:
        metadata_files = tuple(site_packages.glob("*.dist-info/METADATA"))
        for metadata_file in metadata_files:
            name = version = None
            with metadata_file.open("r", encoding="utf-8") as metadata:
                for line in metadata:
                    if line.startswith("Name: "):
                        name = line[6:].strip().lower().replace("_", "-")
                    elif line.startswith("Version: "):
                        version = line[9:].strip()
                    if name is not None and version is not None:
                        break
            if name in _PACKAGE_VERSIONS:
                if name in found:
                    raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
                found[name] = version
    except (OSError, UnicodeError):
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None
    return found


def formal_runtime_preflight(local_config: Any) -> dict[str, Any]:
    """在 worker 啟動前核對 OCR runtime 與 loopback Concept API 設定。"""

    binding = formal_runtime_binding(local_config)
    assert isinstance(local_config, dict)
    _prepare_private_runtime_root(local_config["private_runtime_root"])
    runtime_files = _runtime_files(local_config)
    for runtime_file in runtime_files:
        try:
            file_status = runtime_file.path.stat()
        except OSError:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None
        if not stat.S_ISREG(file_status.st_mode):
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        if (
            runtime_file.expected_size is not None
            and file_status.st_size != runtime_file.expected_size
        ):
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        if _file_sha256(runtime_file.path) != runtime_file.expected_sha256:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    site_packages = Path(local_config["site_packages"])
    if _distribution_versions(site_packages) != _PACKAGE_VERSIONS:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    return binding


def formal_runtime_binding(local_config: Any) -> dict[str, Any]:
    """驗證固定 local-only config，DB 只保存不含 private path 的 exact binding。"""

    if not isinstance(local_config, dict) or set(local_config) != _CONFIG_KEYS:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    for key in _CONFIG_PATH_KEYS:
        value = local_config.get(key)
        if not isinstance(value, str) or not value or "://" in value:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    concept_model = local_config.get("concept_model")
    if not isinstance(concept_model, str) or not concept_model or len(concept_model) > 256:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    concept_kv_cache_bytes = local_config.get("concept_kv_cache_bytes")
    if type(concept_kv_cache_bytes) is not int or concept_kv_cache_bytes < 1:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    concept_max_concurrency = local_config.get("concept_max_concurrency")
    if type(concept_max_concurrency) is not int or concept_max_concurrency not in {1, 2}:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    concept_max_model_len = local_config.get("concept_max_model_len")
    if type(concept_max_model_len) is not int or concept_max_model_len < 1:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    try:
        chat_completions_url(local_config.get("concept_api_base_url"))
    except ConceptAPIError:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None
    runtime_root = Path(local_config["private_runtime_root"])
    if not runtime_root.is_absolute() or runtime_root.is_symlink():
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    try:
        _validate_runtime_lock(local_config["runtime_lock"])
    except (TypeError, ValueError):
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID") from None
    if concept_model != local_config["runtime_lock"]["semantic"]["model_id"]:
        raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")

    repository_root = Path(__file__).resolve().parents[3]
    for relative_path, expected_sha256 in _LOCKED_FILES.items():
        if _file_sha256(repository_root / relative_path) != expected_sha256:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
    code_hashes = {
        relative_path: _file_sha256(repository_root / relative_path)
        for relative_path in _BINDING_FILES
    }
    binding = {
        "schema": "formal-agent1-runtime-binding/v3",
        "runtime_lock_sha256": canonical_sha256(local_config["runtime_lock"]),
        "code_hashes": code_hashes,
        "document_policy": "whole-document-review-aggregation/v1",
        "page_range": {"minimum": 1, "caller_subset": False},
        "call_ceilings": {
            "ocr_calls_per_page": 1,
            "concept_calls_per_page": 2,
            "ocr_initial_loads": 1,
            "concept_initial_loads": 1,
        },
        "timeouts_seconds": {
            "resident_lock": 5,
            "ocr_page": 120,
            "concept_attempt": 300,
            "concept_server_ready": CONCEPT_SERVER_READY_TIMEOUT_SECONDS,
        },
        "retry_policy": {
            "ocr_attempts": 1,
            "concept_attempts": 2,
        },
        "concept_api": {
            "base_url": local_config["concept_api_base_url"],
            "model": concept_model,
            "model_revision": local_config["runtime_lock"]["semantic"]["revision"],
            "model_binding_manifest_sha256": local_config["runtime_lock"]["semantic"][
                "binding_manifest_sha256"
            ],
            "protocol": local_config["runtime_lock"]["semantic"]["api_protocol"],
            "kv_cache_bytes": concept_kv_cache_bytes,
            "max_concurrency": concept_max_concurrency,
            "max_model_len": concept_max_model_len,
        },
        "residency_policy": "ocr-child-then-owned-loopback-concept-server/v3",
        "network_policy": "loopback-concept-api-no-credentials/v1",
        "raw_retention": "none",
    }
    binding["runtime_binding_sha256"] = canonical_sha256(binding)
    return binding


def _source_hash(
    learner_id: UUID, material_id: UUID, artifact_id: UUID, *, dsn: str | None
) -> str:
    try:
        with open_verified_source_pdf(learner_id, artifact_id, dsn=dsn) as source:
            if source.material_id != material_id:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            return source.sha256
    except MaterialProcessingError:
        raise
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_INVALID") from None


def create_material_processing_run(
    learner_id: UUID,
    material_id: UUID,
    source_artifact_id: UUID,
    idempotency_key: str,
    local_config: dict[str, Any],
    *,
    dsn: str | None = None,
) -> MaterialProcessingRun:
    """建立唯一 pending run；client 無法指定頁面、模型或 processing policy。"""

    if not all(
        isinstance(value, UUID)
        for value in (learner_id, material_id, source_artifact_id)
    ):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    runtime_binding = formal_runtime_binding(local_config)
    source_sha256 = _source_hash(
        learner_id, material_id, source_artifact_id, dsn=dsn
    )
    key = _key_digest(idempotency_key)
    fingerprint = _canonical(
        {
            "material_id": str(material_id),
            "source_artifact_id": str(source_artifact_id),
            "source_sha256": source_sha256,
            "runtime_binding": runtime_binding,
        }
    )
    try:
        with database_session(dsn) as session:
            owner = session.execute(
                select(Learner.learner_id)
                .where(Learner.learner_id == learner_id)
                .with_for_update()
            ).scalar_one_or_none()
            if owner is None:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            existing = session.scalar(
                select(MaterialProcessingRunRow)
                .where(
                    MaterialProcessingRunRow.learner_id == learner_id,
                    MaterialProcessingRunRow.idempotency_key_sha256 == key,
                )
                .with_for_update()
            )
            if existing is not None:
                if bytes(existing.request_fingerprint) != fingerprint:
                    raise MaterialProcessingError("MATERIAL_RUN_IDEMPOTENCY_CONFLICT")
                return _row(existing)
            created = _now()
            row = MaterialProcessingRunRow(
                run_id=uuid4(),
                learner_id=learner_id,
                material_id=material_id,
                source_artifact_id=source_artifact_id,
                idempotency_key_sha256=key,
                request_fingerprint=fingerprint,
                runtime_binding=runtime_binding,
                status="pending",
                created_at=created,
                updated_at=created,
            )
            session.add(row)
            session.flush()
            return _row(row)
    except MaterialProcessingError:
        raise
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def read_material_processing_run(
    learner_id: UUID, run_id: UUID, *, dsn: str | None = None
) -> MaterialProcessingRun:
    if not isinstance(learner_id, UUID) or not isinstance(run_id, UUID):
        raise MaterialProcessingError("MATERIAL_RUN_NOT_FOUND")
    try:
        with database_session(dsn) as session:
            found = session.scalar(
                select(MaterialProcessingRunRow).where(
                    MaterialProcessingRunRow.learner_id == learner_id,
                    MaterialProcessingRunRow.run_id == run_id,
                )
            )
        if found is None:
            raise MaterialProcessingError("MATERIAL_RUN_NOT_FOUND")
        return _row(found)
    except MaterialProcessingError:
        raise
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def recover_interrupted_material_runs(*, dsn: str | None = None) -> int:
    """Worker 啟動後先終結舊 running rows，再開始 claim pending。"""

    try:
        with database_session(dsn) as session:
            rows = session.execute(
                update(MaterialProcessingRunRow)
                .where(MaterialProcessingRunRow.status == "running")
                .values(
                    status="failed",
                    error_code="RESTART_INTERRUPTED",
                    completed_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
                .returning(MaterialProcessingRunRow.run_id)
            ).all()
        return len(rows)
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def claim_next_material_processing_run(
    *, dsn: str | None = None
) -> ClaimedMaterialProcessingRun | None:
    """以 PostgreSQL row lock claim 最早 pending run。"""

    try:
        with database_session(dsn) as session:
            found = session.scalar(
                select(MaterialProcessingRunRow)
                .where(MaterialProcessingRunRow.status == "pending")
                .order_by(
                    MaterialProcessingRunRow.created_at,
                    MaterialProcessingRunRow.run_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if found is None:
                return None
            found.status = "running"
            found.updated_at = session.scalar(select(func.clock_timestamp()))
            session.flush()
            return ClaimedMaterialProcessingRun(_row(found))
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def _record_run_failure(run_id: UUID, reason: str, *, dsn: str | None) -> None:
    safe_reason = (
        reason
        if isinstance(reason, str)
        and reason
        and len(reason) <= 100
        and all(character.isupper() or character.isdigit() or character == "_" for character in reason)
        else "MATERIAL_ANALYSIS_FAILED"
    )
    try:
        with database_session(dsn) as session:
            session.execute(
                update(MaterialProcessingRunRow)
                .where(
                    MaterialProcessingRunRow.run_id == run_id,
                    MaterialProcessingRunRow.status == "running",
                )
                .values(
                    status="failed",
                    error_code=safe_reason,
                    completed_at=func.clock_timestamp(),
                    updated_at=func.clock_timestamp(),
                )
            )
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def execute_claimed_material_processing_run(
    claim: ClaimedMaterialProcessingRun,
    local_config: dict[str, Any],
    *,
    dsn: str | None = None,
) -> MaterialProcessingRun:
    """執行 exact whole PDF，驗證 producer bundle 後才發布兩個 revisions。"""

    if not isinstance(claim, ClaimedMaterialProcessingRun):
        raise MaterialProcessingError("MATERIAL_RUN_CLAIM_INVALID")
    run = claim.run
    try:
        if formal_runtime_binding(local_config) != run.runtime_binding:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        source_sha256 = _source_hash(
            run.learner_id, run.material_id, run.source_artifact_id, dsn=dsn
        )
        with tempfile.TemporaryDirectory(prefix="studydy-material-run-") as directory:
            private = Path(directory)
            private.chmod(0o700)
            source_path = private / "source.pdf"
            with open_verified_source_pdf(
                run.learner_id, run.source_artifact_id, dsn=dsn
            ) as source:
                with source_path.open("xb") as destination:
                    while chunk := source.file.read(_CHUNK):
                        destination.write(chunk)
            producer_run_id = f"text-first-run:{run.run_id}"
            run_full_text_first_pdf(
                {
                    "media_type": "application/pdf",
                    "source_path": str(source_path),
                    "expected_source_sha256": source_sha256,
                },
                deepcopy(local_config),
                run_id=producer_run_id,
                produced_at=_now().isoformat(),
                runtime_binding_sha256=run.runtime_binding[
                    "runtime_binding_sha256"
                ],
            )
        producer_bundle = read_producer_bundle(
            Path(local_config["private_runtime_root"]), producer_run_id
        )
        bundle = producer_bundle["bundle"]
        if bundle["processing"] == "failed":
            _record_run_failure(
                run.run_id,
                bundle["reason_codes"][0]
                if bundle["reason_codes"]
                else "MATERIAL_ANALYSIS_FAILED",
                dsn=dsn,
            )
            return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)
        publish_material_outputs(
            run.learner_id,
            run.material_id,
            run.run_id,
            source_sha256,
            run.runtime_binding["runtime_binding_sha256"],
            producer_bundle,
            dsn=dsn,
        )
    except MaterialRunOutputError:
        _record_run_failure(run.run_id, "MATERIAL_OUTPUT_FAILED", dsn=dsn)
    except MaterialProcessingError as error:
        _record_run_failure(run.run_id, str(error), dsn=dsn)
    except Exception:
        _record_run_failure(run.run_id, "MATERIAL_ANALYSIS_FAILED", dsn=dsn)
    return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)

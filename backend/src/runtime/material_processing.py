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
    SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
    ConceptAPIError,
    chat_completions_url,
    preflight_semantic_service,
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
    "verifier_model_root",
    "concept_api_base_url",
    "concept_model",
    "concept_max_concurrency",
    "concept_max_model_len",
}
_CONFIG_PATH_KEYS = {
    "private_runtime_root",
    "python_executable",
    "site_packages",
    "ocr_model_root",
    "verifier_model_root",
}
_RUNTIME_COMPONENTS = {
    "layout",
    "runtime_lock",
    "python_runtime",
    "ocr_package",
    "ocr_model",
    "verifier_model",
    "concept_runtime",
    "concept_model",
}
_RUNTIME_REASONS = {
    "LOCAL_RUNTIME_MISSING",
    "LOCAL_RUNTIME_UNSAFE_TARGET",
    "LOCAL_RUNTIME_NOT_EXECUTABLE",
    "LOCAL_RUNTIME_VERSION_MISMATCH",
    "LOCAL_RUNTIME_SMOKE_FAILED",
    "LOCAL_RUNTIME_SETTINGS_MISMATCH",
    "LOCAL_RUNTIME_LOCK_MISMATCH",
    "LOCAL_RUNTIME_WRITE_FAILED",
}


class MaterialProcessingError(RuntimeError):
    """Material processing 失敗且不揭露教材、設定或資料庫細節。"""

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.component = component if component in _RUNTIME_COMPONENTS else None
        self.reason = reason if reason in _RUNTIME_REASONS else None


def _runtime_error(component: str, reason: str) -> MaterialProcessingError:
    return MaterialProcessingError(
        "MATERIAL_CONFIGURATION_INVALID",
        component=component,
        reason=reason,
    )


@dataclass(frozen=True)
class MaterialProcessingRun:
    run_id: UUID
    learner_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    runtime_binding: dict[str, Any] = field(repr=False)
    status: str
    progress_stage: str
    completed_pages: int
    total_pages: int | None
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
        progress_stage=row.progress_stage,
        completed_pages=row.completed_pages,
        total_pages=row.total_pages,
        error_code=row.error_code,
        output_binding=deepcopy(row.output_binding),
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _absolute_runtime_path(
    value: str, *, is_directory: bool, component: str
) -> Path:
    """確認 runtime 路徑存在且類型正確；正常 symlink 由作業系統解析。"""

    path = Path(value)
    try:
        path_status = path.stat()
    except FileNotFoundError:
        raise _runtime_error(component, "LOCAL_RUNTIME_MISSING") from None
    except OSError:
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET") from None
    expected_kind = stat.S_ISDIR if is_directory else stat.S_ISREG
    if not path.is_absolute() or not expected_kind(path_status.st_mode):
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET")
    return path


def _prepare_private_runtime_root(value: str) -> None:
    """建立 runtime root，並確認目前 process 可讀寫及進入。"""

    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise _runtime_error("layout", "LOCAL_RUNTIME_UNSAFE_TARGET")
    try:
        path.mkdir(parents=True, exist_ok=True)
        path_status = path.stat()
    except FileExistsError:
        raise _runtime_error("layout", "LOCAL_RUNTIME_UNSAFE_TARGET") from None
    except OSError:
        raise _runtime_error("layout", "LOCAL_RUNTIME_WRITE_FAILED") from None
    if (
        not stat.S_ISDIR(path_status.st_mode)
        or not os.access(path, os.R_OK | os.W_OK | os.X_OK)
    ):
        raise _runtime_error("layout", "LOCAL_RUNTIME_UNSAFE_TARGET")


def _installed_package_versions(
    site_packages: Path,
    expected_packages: dict[str, str],
) -> dict[str, str]:
    """讀取必要 package metadata；model smoke另驗證實際 imports。"""

    found: dict[str, str] = {}
    try:
        for metadata_file in site_packages.glob("*.dist-info/METADATA"):
            name = version = None
            with metadata_file.open("r", encoding="utf-8") as metadata:
                for line in metadata:
                    if line.startswith("Name: "):
                        name = line[6:].strip().lower().replace("_", "-")
                    elif line.startswith("Version: "):
                        version = line[9:].strip()
                    if name is not None and version is not None:
                        break
            if name in expected_packages:
                found[name] = version
    except (OSError, UnicodeError):
        raise _runtime_error(
            "ocr_package", "LOCAL_RUNTIME_VERSION_MISMATCH"
        ) from None
    return found


def validate_installed_local_runtime(
    local_config: Any,
) -> dict[str, Any]:
    """驗證可執行的Python環境與OCR/verifier model入口。"""

    binding = formal_runtime_binding(local_config)
    assert isinstance(local_config, dict)
    python_executable = _absolute_runtime_path(
        local_config["python_executable"],
        is_directory=False,
        component="python_runtime",
    )
    if not os.access(python_executable, os.X_OK):
        raise _runtime_error("python_runtime", "LOCAL_RUNTIME_NOT_EXECUTABLE")
    site_packages = _absolute_runtime_path(
        local_config["site_packages"],
        is_directory=True,
        component="ocr_package",
    )
    for key, component in (
        ("ocr_model_root", "ocr_model"),
        ("verifier_model_root", "verifier_model"),
    ):
        model_root = _absolute_runtime_path(
            local_config[key], is_directory=True, component=component
        )
        _absolute_runtime_path(
            str(model_root / "config.json"),
            is_directory=False,
            component=component,
        )
    expected_packages = local_config["runtime_lock"]["packages"]
    if (
        _installed_package_versions(site_packages, expected_packages)
        != expected_packages
    ):
        raise _runtime_error("ocr_package", "LOCAL_RUNTIME_VERSION_MISMATCH")
    return binding


def formal_runtime_preflight(local_config: Any) -> dict[str, Any]:
    """唯讀驗證成功後，才準備本次執行使用的 private root。"""

    binding = validate_installed_local_runtime(local_config)
    assert isinstance(local_config, dict)
    try:
        preflight_semantic_service(local_config)
    except ConceptAPIError as error:
        reason = (
            "LOCAL_RUNTIME_SETTINGS_MISMATCH"
            if error.reason_code
            in {"CONCEPT_API_CONFIG_INVALID", "CONCEPT_API_IDENTITY_MISMATCH"}
            else "LOCAL_RUNTIME_MISSING"
        )
        raise _runtime_error("concept_runtime", reason) from None
    _prepare_private_runtime_root(local_config["private_runtime_root"])
    return binding


def formal_runtime_binding(local_config: Any) -> dict[str, Any]:
    """驗證固定 local-only config，DB 只保存不含 private path 的 exact binding。"""

    if not isinstance(local_config, dict) or set(local_config) != _CONFIG_KEYS:
        raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    for key in _CONFIG_PATH_KEYS:
        value = local_config.get(key)
        if not isinstance(value, str) or not value or "://" in value:
            raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    site_packages = Path(local_config["site_packages"])
    try:
        root = site_packages.parents[4]
    except IndexError:
        raise _runtime_error(
            "layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH"
        ) from None
    expected_paths = {
        "private_runtime_root": root / "runtime",
        "python_executable": root / "ocr/runtime/bin/python3.12",
        "site_packages": root / "ocr/runtime/lib/python3.12/site-packages",
        "ocr_model_root": root / "models/unlimited-ocr",
        "verifier_model_root": root / "models/mdeberta-v3-base-mnli-xnli",
    }
    if any(
        Path(local_config[name]) != expected
        for name, expected in expected_paths.items()
    ):
        raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    concept_model = local_config.get("concept_model")
    if not isinstance(concept_model, str) or not concept_model or len(concept_model) > 256:
        raise _runtime_error("concept_model", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    concept_max_concurrency = local_config.get("concept_max_concurrency")
    if type(concept_max_concurrency) is not int or concept_max_concurrency != 1:
        raise _runtime_error("concept_runtime", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    concept_max_model_len = local_config.get("concept_max_model_len")
    if type(concept_max_model_len) is not int or concept_max_model_len != 32_768:
        raise _runtime_error("concept_runtime", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    try:
        chat_completions_url(local_config.get("concept_api_base_url"))
    except ConceptAPIError:
        raise _runtime_error(
            "concept_runtime", "LOCAL_RUNTIME_SETTINGS_MISMATCH"
        ) from None
    if local_config["concept_api_base_url"] != "http://127.0.0.1:8000":
        raise _runtime_error(
            "concept_runtime", "LOCAL_RUNTIME_SETTINGS_MISMATCH"
        )
    runtime_root = Path(local_config["private_runtime_root"])
    if not runtime_root.is_absolute() or runtime_root.is_symlink():
        raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    try:
        _validate_runtime_lock(local_config["runtime_lock"])
    except (TypeError, ValueError):
        raise _runtime_error(
            "runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH"
        ) from None
    if concept_model != local_config["runtime_lock"]["semantic"]["model_id"]:
        raise _runtime_error("concept_model", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    semantic_lock = local_config["runtime_lock"]["semantic"]
    if (
        semantic_lock["server"]
        != {
            "package": "vllm",
            "version": "0.28.0",
            "python_minor": "3.12",
            "torch": "2.13.0+cu130",
            "cuda": "13.0",
            "transformers": "5.16.1",
        }
        or semantic_lock["service"]
        != {
            "host": "127.0.0.1",
            "port": 8000,
            "max_model_len": 32768,
            "max_num_seqs": 1,
            "authentication": "environment-bearer:VLLM_API_KEY",
        }
    ):
        raise _runtime_error(
            "concept_runtime", "LOCAL_RUNTIME_SETTINGS_MISMATCH"
        )

    binding = {
        "schema": "formal-material-runtime-binding/v9",
        "runtime_lock_sha256": canonical_sha256(local_config["runtime_lock"]),
        "document_policy": "whole-document-review-aggregation/v1",
        "page_range": {"minimum": 1, "caller_subset": False},
        "call_ceilings": {
            "ocr_calls_per_page": 1,
            "ocr_initial_loads": 1,
            "backend_concept_initial_loads": 0,
            "concept_equivalence_initial_loads": 1,
            "concept_equivalence_pairs_per_material": 16,
            "concept_equivalence_directions_per_material": 32,
        },
        "timeouts_seconds": {
            "resident_lock": 5,
            "ocr_page": 120,
            "concept_attempt": 300,
            "semantic_service_preflight": SEMANTIC_SERVICE_PREFLIGHT_TIMEOUT_SECONDS,
            "concept_equivalence": local_config["runtime_lock"][
                "concept_equivalence"
            ]["timeout_seconds"],
        },
        "retry_policy": {
            "ocr_attempts": 1,
            "concept_attempts": 2,
        },
        "concept_api": {
            "base_url": local_config["concept_api_base_url"],
            "model": concept_model,
            "model_revision": local_config["runtime_lock"]["semantic"]["revision"],
            "protocol": local_config["runtime_lock"]["semantic"]["api_protocol"],
            "max_concurrency": concept_max_concurrency,
            "max_model_len": concept_max_model_len,
            "server": deepcopy(semantic_lock["server"]),
            "structured_output": deepcopy(semantic_lock["structured_output"]),
            "input_token_budget": deepcopy(semantic_lock["input_token_budget"]),
        },
        "verifier_model": deepcopy(
            local_config["runtime_lock"]["verifier_model"]
        ),
        "concept_equivalence": deepcopy(
            local_config["runtime_lock"]["concept_equivalence"]
        ),
        "residency_policy": (
            "ocr-child-with-external-resident-loopback-semantic-service-"
            "then-concept-equivalence/v8"
        ),
        "network_policy": "fixed-loopback-semantic-api-environment-bearer/v1",
        "retention_policy": {
            "provider_raw": "not_persisted",
            "validated_cache": "local_private_cache",
            "run_handoff": "deleted_before_terminal_publish",
        },
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
                progress_stage="queued",
                completed_pages=0,
                total_pages=None,
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


_NEXT_PROGRESS_STAGE = {
    "queued": "page_evidence",
    "page_evidence": "concept_generation",
    "concept_generation": "knowledge_map_generation",
    "knowledge_map_generation": "publishing",
}


def _record_material_progress(
    run_id: UUID,
    stage: str,
    completed_pages: int,
    total_pages: int,
    *,
    dsn: str | None,
) -> None:
    """只更新目前 running run 的真實 stage 與已完成頁數。"""

    if (
        not isinstance(run_id, UUID)
        or stage not in set(_NEXT_PROGRESS_STAGE.values())
        or type(completed_pages) is not int
        or type(total_pages) is not int
        or total_pages < 1
        or not 0 <= completed_pages <= total_pages
        or (
            stage in {"knowledge_map_generation", "publishing"}
            and completed_pages != total_pages
        )
    ):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    try:
        with database_session(dsn) as session:
            row = session.scalar(
                select(MaterialProcessingRunRow)
                .where(
                    MaterialProcessingRunRow.run_id == run_id,
                    MaterialProcessingRunRow.status == "running",
                )
                .with_for_update()
            )
            if row is None:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            if row.total_pages is not None and row.total_pages != total_pages:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            if stage == row.progress_stage:
                if completed_pages < row.completed_pages:
                    raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            elif _NEXT_PROGRESS_STAGE.get(row.progress_stage) != stage:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            elif (
                row.progress_stage != "queued"
                and (
                    row.total_pages is None
                    or row.completed_pages != row.total_pages
                )
            ):
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            row.progress_stage = stage
            row.completed_pages = completed_pages
            row.total_pages = total_pages
            row.updated_at = session.scalar(select(func.clock_timestamp()))
            session.flush()
    except MaterialProcessingError:
        raise
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

    def progress_callback(stage: str, completed: int, total: int) -> None:
        _record_material_progress(
            run.run_id,
            stage,
            completed,
            total,
            dsn=dsn,
        )

    try:
        if formal_runtime_preflight(local_config) != run.runtime_binding:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        with tempfile.TemporaryDirectory(prefix="studydy-material-run-") as directory:
            private = Path(directory)
            private.chmod(0o700)
            source_path = private / "source.pdf"
            with open_verified_source_pdf(
                run.learner_id, run.source_artifact_id, dsn=dsn
            ) as source:
                if source.material_id != run.material_id:
                    raise MaterialProcessingError("MATERIAL_RUN_INVALID")
                source_sha256 = source.sha256
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
                progress_callback=progress_callback,
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
        page_count = bundle["page_count"]
        progress_callback("knowledge_map_generation", page_count, page_count)
        publish_material_outputs(
            run.learner_id,
            run.material_id,
            run.run_id,
            source_sha256,
            run.runtime_binding["runtime_binding_sha256"],
            producer_bundle,
            local_config=deepcopy(local_config),
            runtime_root=Path(local_config["private_runtime_root"]),
            progress_callback=progress_callback,
            dsn=dsn,
        )
    except OSError as error:
        _record_run_failure(run.run_id, str(error), dsn=dsn)
    except MaterialRunOutputError as error:
        _record_run_failure(run.run_id, str(error), dsn=dsn)
    except MaterialProcessingError as error:
        _record_run_failure(run.run_id, str(error), dsn=dsn)
    except Exception:
        _record_run_failure(run.run_id, "MATERIAL_ANALYSIS_FAILED", dsn=dsn)
    return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)

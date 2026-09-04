from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from pdf_evidence.material_pipeline import MaterialAnalysisError, analyze_material, validate_runtime_lock
from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.semantic_service import SemanticServiceError, preflight_semantic_service

from .storage.artifacts import open_verified_source_pdf
from .storage.knowledge_structures import KnowledgeStructureStoreError, publish_knowledge_structure
from .storage.tables import Learner, MaterialProcessingRun as RunRow, database_session


_CONFIG_KEYS = {"private_runtime_root", "runtime_lock", "python_executable", "site_packages", "ocr_model_root"}
_RUNTIME_COMPONENTS = {"layout", "runtime_lock", "python_runtime", "ocr_package", "ocr_model", "semantic_service"}
_RUNTIME_REASONS = {
    "LOCAL_RUNTIME_MISSING", "LOCAL_RUNTIME_UNSAFE_TARGET", "LOCAL_RUNTIME_NOT_EXECUTABLE",
    "LOCAL_RUNTIME_VERSION_MISMATCH", "LOCAL_RUNTIME_SMOKE_FAILED",
    "LOCAL_RUNTIME_SETTINGS_MISMATCH", "LOCAL_RUNTIME_LOCK_MISMATCH", "LOCAL_RUNTIME_WRITE_FAILED",
}


class MaterialProcessingError(RuntimeError):
    def __init__(self, message: str, *, component: str | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.component = component if component in _RUNTIME_COMPONENTS else None
        self.reason = reason if reason in _RUNTIME_REASONS else None


def _runtime_error(component: str, reason: str) -> MaterialProcessingError:
    return MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID", component=component, reason=reason)


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


def _row(row: RunRow) -> MaterialProcessingRun:
    return MaterialProcessingRun(
        row.run_id, row.learner_id, row.material_id, row.source_artifact_id,
        deepcopy(row.runtime_binding), row.status, row.progress_stage,
        row.completed_pages, row.total_pages, row.error_code,
        deepcopy(row.output_binding), row.created_at, row.updated_at, row.completed_at,
    )


def _digest(value: Any) -> bytes:
    try:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).digest()
    except (TypeError, ValueError):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID") from None


def _key(value: str) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value.encode()) <= 256:
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    return sha256(value.encode()).digest()


def _existing_path(value: str, *, directory: bool, component: str) -> Path:
    try:
        path = Path(value)
        mode = path.stat().st_mode
    except (OSError, TypeError):
        raise _runtime_error(component, "LOCAL_RUNTIME_MISSING") from None
    if not path.is_absolute() or (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)) is False:
        raise _runtime_error(component, "LOCAL_RUNTIME_UNSAFE_TARGET")
    return path


def runtime_binding(local_config: Any) -> dict[str, Any]:
    if not isinstance(local_config, dict) or set(local_config) != _CONFIG_KEYS:
        raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    try:
        root = Path(local_config["private_runtime_root"])
        site_packages = Path(local_config["site_packages"])
        install_root = site_packages.parents[4]
        expected = {
            "private_runtime_root": install_root / "runtime",
            "python_executable": install_root / "ocr/runtime/bin/python3.12",
            "site_packages": install_root / "ocr/runtime/lib/python3.12/site-packages",
            "ocr_model_root": install_root / "models/unlimited-ocr",
        }
        if root.is_symlink() or any(Path(local_config[key]) != path for key, path in expected.items()):
            raise ValueError
        lock = validate_runtime_lock(local_config["runtime_lock"])
    except (IndexError, KeyError, MaterialAnalysisError, TypeError, ValueError):
        raise _runtime_error("runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH") from None
    binding = {
        "schema": "material-runtime-binding/v1",
        "python": lock["python"],
        "runtime_lock_sha256": canonical_sha256(lock),
        "model_id": lock["semantic_service"]["model_id"],
        "model_revision": lock["semantic_service"]["revision"],
        "semantic_service": {
            "base_url": lock["semantic_service"]["base_url"],
            "max_model_len": lock["semantic_service"]["max_model_len"],
            "server": deepcopy(lock["semantic_service"]["server"]),
        },
        "ocr": {"model_id": lock["ocr"]["model_id"], "revision": lock["ocr"]["revision"]},
        "policy": "evidence-unified-semantics-product/v1",
    }
    binding["runtime_binding_sha256"] = canonical_sha256(binding)
    return binding


def validate_installed_local_runtime(local_config: Any) -> dict[str, Any]:
    binding = runtime_binding(local_config)
    assert isinstance(local_config, dict)
    executable = _existing_path(local_config["python_executable"], directory=False, component="python_runtime")
    if not os.access(executable, os.X_OK):
        raise _runtime_error("python_runtime", "LOCAL_RUNTIME_NOT_EXECUTABLE")
    site_packages = _existing_path(local_config["site_packages"], directory=True, component="ocr_package")
    model_root = _existing_path(local_config["ocr_model_root"], directory=True, component="ocr_model")
    _existing_path(str(model_root / "config.json"), directory=False, component="ocr_model")
    lock = local_config["runtime_lock"]
    expected = lock["packages"]
    try:
        distributions = importlib.metadata.distributions(path=[str(site_packages)])
        installed = {
            distribution.metadata["Name"].lower().replace("_", "-"): distribution.version
            for distribution in distributions
            if distribution.metadata.get("Name")
        }
    except Exception:
        raise _runtime_error("ocr_package", "LOCAL_RUNTIME_VERSION_MISMATCH") from None
    if any(installed.get(name.replace("_", "-")) != version for name, version in expected.items() if name != "backend"):
        raise _runtime_error("ocr_package", "LOCAL_RUNTIME_VERSION_MISMATCH")
    return binding


def _prepare_runtime_root(value: str) -> None:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise _runtime_error("layout", "LOCAL_RUNTIME_UNSAFE_TARGET")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            raise OSError
    except OSError:
        raise _runtime_error("layout", "LOCAL_RUNTIME_WRITE_FAILED") from None


def runtime_preflight(local_config: Any) -> dict[str, Any]:
    binding = validate_installed_local_runtime(local_config)
    assert isinstance(local_config, dict)
    try:
        preflight_semantic_service(local_config["runtime_lock"])
    except SemanticServiceError as error:
        reason = "LOCAL_RUNTIME_SETTINGS_MISMATCH" if error.reason_code.endswith(("CONFIG_INVALID", "IDENTITY_MISMATCH")) else "LOCAL_RUNTIME_MISSING"
        raise _runtime_error("semantic_service", reason) from None
    _prepare_runtime_root(local_config["private_runtime_root"])
    return binding


def _source_hash(learner_id: UUID, material_id: UUID, artifact_id: UUID, *, dsn: str | None) -> str:
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
    if not all(isinstance(value, UUID) for value in (learner_id, material_id, source_artifact_id)):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    binding = runtime_binding(local_config)
    source_sha256 = _source_hash(learner_id, material_id, source_artifact_id, dsn=dsn)
    key = _key(idempotency_key)
    fingerprint = _digest({
        "material_id": str(material_id), "source_artifact_id": str(source_artifact_id),
        "source_sha256": source_sha256, "runtime_binding": binding,
    })
    try:
        with database_session(dsn) as session:
            if session.scalar(select(Learner.learner_id).where(Learner.learner_id == learner_id).with_for_update()) is None:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            existing = session.scalar(select(RunRow).where(RunRow.learner_id == learner_id, RunRow.idempotency_key_sha256 == key).with_for_update())
            if existing is not None:
                if bytes(existing.request_fingerprint) != fingerprint:
                    raise MaterialProcessingError("MATERIAL_RUN_IDEMPOTENCY_CONFLICT")
                return _row(existing)
            now = datetime.now(UTC)
            created = RunRow(
                run_id=uuid4(), learner_id=learner_id, material_id=material_id,
                source_artifact_id=source_artifact_id, idempotency_key_sha256=key,
                request_fingerprint=fingerprint, runtime_binding=binding, status="pending",
                progress_stage="queued", completed_pages=0, total_pages=None,
                created_at=now, updated_at=now,
            )
            session.add(created)
            session.flush()
            return _row(created)
    except MaterialProcessingError:
        raise
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def read_material_processing_run(learner_id: UUID, run_id: UUID, *, dsn: str | None = None) -> MaterialProcessingRun:
    try:
        with database_session(dsn) as session:
            found = session.scalar(select(RunRow).where(RunRow.learner_id == learner_id, RunRow.run_id == run_id))
        if found is None:
            raise MaterialProcessingError("MATERIAL_RUN_NOT_FOUND")
        return _row(found)
    except MaterialProcessingError:
        raise
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def recover_interrupted_material_runs(*, dsn: str | None = None) -> int:
    try:
        with database_session(dsn) as session:
            rows = session.execute(
                update(RunRow).where(RunRow.status == "running").values(
                    status="failed", error_code="RESTART_INTERRUPTED",
                    completed_at=func.clock_timestamp(), updated_at=func.clock_timestamp(),
                ).returning(RunRow.run_id)
            ).all()
        return len(rows)
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def claim_next_material_processing_run(*, dsn: str | None = None) -> ClaimedMaterialProcessingRun | None:
    try:
        with database_session(dsn) as session:
            row = session.scalar(select(RunRow).where(RunRow.status == "pending").order_by(RunRow.created_at, RunRow.run_id).with_for_update(skip_locked=True).limit(1))
            if row is None:
                return None
            row.status = "running"
            row.updated_at = session.scalar(select(func.clock_timestamp()))
            session.flush()
            return ClaimedMaterialProcessingRun(_row(row))
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


_NEXT_STAGE = {"queued": "evidence", "evidence": "semantics", "semantics": "publishing"}


def _record_progress(run_id: UUID, stage: str, completed: int, total: int, *, dsn: str | None) -> None:
    if stage not in _NEXT_STAGE.values() or type(completed) is not int or type(total) is not int or not 0 <= completed <= total or total < 1:
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    try:
        with database_session(dsn) as session:
            row = session.scalar(select(RunRow).where(RunRow.run_id == run_id, RunRow.status == "running").with_for_update())
            if row is None or (row.total_pages is not None and row.total_pages != total):
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            if row.progress_stage == stage:
                if completed < row.completed_pages:
                    raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            elif _NEXT_STAGE.get(row.progress_stage) != stage:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            elif row.progress_stage != "queued" and row.completed_pages != total:
                raise MaterialProcessingError("MATERIAL_RUN_INVALID")
            row.progress_stage, row.completed_pages, row.total_pages = stage, completed, total
            row.updated_at = session.scalar(select(func.clock_timestamp()))
    except MaterialProcessingError:
        raise
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def _record_failure(run_id: UUID, reason: str, *, dsn: str | None) -> None:
    safe = reason if isinstance(reason, str) and 1 <= len(reason) <= 100 and all(character.isupper() or character.isdigit() or character == "_" for character in reason) else "MATERIAL_ANALYSIS_FAILED"
    try:
        with database_session(dsn) as session:
            session.execute(update(RunRow).where(RunRow.run_id == run_id, RunRow.status == "running").values(
                status="failed", error_code=safe, completed_at=func.clock_timestamp(), updated_at=func.clock_timestamp(),
            ))
    except Exception:
        raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None


def execute_claimed_material_processing_run(
    claim: ClaimedMaterialProcessingRun,
    local_config: dict[str, Any],
    *,
    dsn: str | None = None,
) -> MaterialProcessingRun:
    if not isinstance(claim, ClaimedMaterialProcessingRun):
        raise MaterialProcessingError("MATERIAL_RUN_CLAIM_INVALID")
    run = claim.run
    try:
        if runtime_preflight(local_config) != run.runtime_binding:
            raise MaterialProcessingError("MATERIAL_CONFIGURATION_INVALID")
        with tempfile.TemporaryDirectory(prefix="studydy-material-") as directory:
            source_path = Path(directory) / "source.pdf"
            with open_verified_source_pdf(run.learner_id, run.source_artifact_id, dsn=dsn) as source:
                if source.material_id != run.material_id:
                    raise MaterialProcessingError("MATERIAL_RUN_INVALID")
                with source_path.open("xb") as destination:
                    while chunk := source.file.read(1024 * 1024):
                        destination.write(chunk)
                source_sha256 = source.sha256
            structure = analyze_material(
                {"media_type": "application/pdf", "source_path": str(source_path), "expected_source_sha256": source_sha256},
                deepcopy(local_config),
                run_id=str(run.run_id),
                progress_callback=lambda stage, completed, total: _record_progress(run.run_id, stage, completed, total, dsn=dsn),
            )
        if structure["status"]["processing"] == "failed":
            raise MaterialProcessingError("NO_CANONICAL_CONCEPT")
        _record_progress(run.run_id, "publishing", structure["page_count"], structure["page_count"], dsn=dsn)
        publish_knowledge_structure(run.learner_id, run.material_id, run.run_id, structure, dsn=dsn)
    except (KnowledgeStructureStoreError, MaterialAnalysisError, MaterialProcessingError) as error:
        _record_failure(run.run_id, getattr(error, "reason_code", None) or str(error), dsn=dsn)
    except Exception:
        _record_failure(run.run_id, "MATERIAL_ANALYSIS_FAILED", dsn=dsn)
    return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)

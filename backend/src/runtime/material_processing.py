from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import tempfile
from typing import Any, BinaryIO, Sequence
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from pdf_evidence.pipeline.run import RUN_SCHEMA, development_pipeline_binding, run_development_pdf
from pdf_evidence.study_material_output import validate_study_material_output

from .storage.artifacts import open_verified_source_pdf, publish_resource_pdf, remove_resource_pdf
from .storage.material_outputs import MaterialRunOutputError, publish_terminal_outputs, store_resource_catalog
from .storage.tables import (
    Learner,
    MaterialProcessingRun as MaterialProcessingRunRow,
    ResourceCatalog,
    database_session,
)

_RESOURCE_LIMIT = 104_857_600
_RESOURCE_COUNT = range(1, 5)
_CHUNK = 1024 * 1024
_PROVIDER_COUNT_KEYS = {"page_structure", "visual_alignment_adjudication", "concept_candidate", "concept_content", "total"}
_DEVELOPMENT_RUN_RESULT_KEYS = {
    "schema", "development_only", "run_id", "input_binding", "processing",
    "quality", "decision", "reason_code", "provider_call_counts", "cache_hits",
    "page_statuses", "study_material_output",
}
_SAFE_DEVELOPMENT_RUN_OUTCOMES = {
    ("partial", "needs_review", "review", "DEVELOPMENT_FULL_DOCUMENT_PARTIAL"),
    ("succeeded", "needs_review", "review", "DEVELOPMENT_OUTPUT_NEEDS_REVIEW"),
    ("succeeded", "accepted", "retain", "DEVELOPMENT_OUTPUT_ACCEPTED"),
}
_SAFE_PROVIDER_ERRORS = {
    "LOCAL_PROVIDER_TIMEOUT", "LOCAL_PROVIDER_RATE_LIMITED", "LOCAL_PROVIDER_TRANSIENT_ERROR"
}


class MaterialProcessingError(RuntimeError):
    """Material processing 失敗且不揭露教材、設定或資料庫細節。"""


@dataclass(frozen=True)
class ControlledResourceUpload:
    title: str
    topics: list[str]
    keywords: list[str]
    source_locator: str = field(repr=False)
    license_status: str
    use_boundary: str
    checked_at: str
    learning_use: str
    source: BinaryIO = field(repr=False, compare=False)


@dataclass(frozen=True)
class MaterialProcessingRun:
    run_id: UUID
    learner_id: UUID
    material_id: UUID
    source_artifact_id: UUID
    subject: str
    page_limit: int
    runtime_binding: dict[str, Any]
    catalog_revision: str | None
    status: str
    error_code: str | None
    output_binding: dict[str, Any] | None
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
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
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
        subject=row.subject,
        page_limit=row.page_limit,
        runtime_binding=deepcopy(row.runtime_binding),
        catalog_revision=row.catalog_revision,
        status=row.status,
        error_code=row.error_code,
        output_binding=deepcopy(row.output_binding),
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


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


def _valid_locator(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _stage_resources(
    uploads: Sequence[ControlledResourceUpload], root: Path, subject: str
) -> list[tuple[Path, dict[str, Any], str, int]]:
    if isinstance(uploads, (str, bytes)):
        raise MaterialProcessingError("CONTROLLED_RESOURCE_INVALID")
    try:
        items = list(uploads)
    except Exception:
        raise MaterialProcessingError("CONTROLLED_RESOURCE_INVALID") from None
    if len(items) not in _RESOURCE_COUNT:
        raise MaterialProcessingError("CONTROLLED_RESOURCE_INVALID")
    staged = []
    for index, upload in enumerate(items):
        if (
            not isinstance(upload, ControlledResourceUpload)
            or not _valid_locator(upload.source_locator)
            or not hasattr(upload.source, "read")
        ):
            raise MaterialProcessingError("CONTROLLED_RESOURCE_INVALID")
        path = root / f"resource-{index}.pdf"
        digest = sha256()
        size = 0
        try:
            with path.open("xb") as destination:
                while True:
                    chunk = upload.source.read(_CHUNK)
                    if not isinstance(chunk, bytes):
                        raise OSError
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _RESOURCE_LIMIT:
                        raise OSError
                    destination.write(chunk)
                    digest.update(chunk)
            if path.read_bytes()[:5] != b"%PDF-" or b"%%EOF" not in path.read_bytes()[-1024:]:
                raise OSError
        except Exception as failure:
            raise MaterialProcessingError("CONTROLLED_RESOURCE_INVALID") from None
        metadata = {
            "title": upload.title,
            "topics": deepcopy(upload.topics),
            "keywords": deepcopy(upload.keywords),
            "source_locator": upload.source_locator,
            "license_status": upload.license_status,
            "use_boundary": upload.use_boundary,
            "checked_at": upload.checked_at,
            "learning_use": upload.learning_use,
            "subject": subject,
        }
        staged.append((path, metadata, digest.hexdigest(), size))
    staged.sort(key=lambda item: (item[1]["title"], item[1]["source_locator"], item[2]))
    return staged


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
    subject: str,
    idempotency_key: str,
    resource_uploads: Sequence[ControlledResourceUpload],
    local_config: dict[str, Any],
    *,
    page_limit: int,
    dsn: str | None = None,
) -> MaterialProcessingRun:
    """建立 preparation row、發布 controlled resources，成功後轉成 pending。"""
    if (
        not all(isinstance(value, UUID) for value in (learner_id, material_id, source_artifact_id))
        or not isinstance(subject, str)
        or re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", subject) is None
        or isinstance(page_limit, bool)
        or not isinstance(page_limit, int)
        or not 1 <= page_limit <= 1000
    ):
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    runtime_binding = development_pipeline_binding(local_config)
    if runtime_binding is None:
        raise MaterialProcessingError("MATERIAL_RUN_INVALID")
    source_sha256 = _source_hash(learner_id, material_id, source_artifact_id, dsn=dsn)
    key = _key_digest(idempotency_key)
    with tempfile.TemporaryDirectory(prefix="studydy-resource-input-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        staged = _stage_resources(resource_uploads, root, subject)
        fingerprint = _canonical(
            {
                "material_id": str(material_id),
                "source_artifact_id": str(source_artifact_id),
                "source_sha256": source_sha256,
                "subject": subject,
                "page_limit": page_limit,
                "runtime_binding": runtime_binding,
                "resources": [
                    {**metadata, "artifact_sha256": digest, "size_bytes": size}
                    for _, metadata, digest, size in staged
                ],
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
                run_id = uuid4()
                created = _now()
                session.add(
                    MaterialProcessingRunRow(
                        run_id=run_id,
                        learner_id=learner_id,
                        material_id=material_id,
                        source_artifact_id=source_artifact_id,
                        subject=subject,
                        page_limit=page_limit,
                        idempotency_key_sha256=key,
                        request_fingerprint=fingerprint,
                        runtime_binding=runtime_binding,
                        status="running",
                        created_at=created,
                        updated_at=created,
                    )
                )
        except MaterialProcessingError:
            raise
        except Exception as failure:
            raise MaterialProcessingError("MATERIAL_RUN_STORAGE_FAILED") from None

        published: list[UUID] = []
        try:
            candidates = []
            for path, metadata, digest, size in staged:
                artifact_id = uuid4()
                with path.open("rb") as source:
                    publish_resource_pdf(
                        learner_id, material_id, artifact_id, source, digest, size, dsn=dsn
                    )
                published.append(artifact_id)
                candidates.append(
                    {
                        "assessment": "accepted",
                        **deepcopy(metadata),
                        "artifact_ref": f"objects/{artifact_id.hex}",
                        "artifact_sha256": digest,
                    }
                )
            store_resource_catalog(
                learner_id, material_id, run_id, subject, candidates, dsn=dsn
            )
        except Exception as failure:
            cleanup_failed = False
            for artifact_id in published:
                try:
                    remove_resource_pdf(learner_id, material_id, artifact_id, dsn=dsn)
                except Exception:
                    cleanup_failed = True
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
                            error_code="CONTROLLED_RESOURCE_INVALID",
                            completed_at=func.clock_timestamp(),
                            updated_at=func.clock_timestamp(),
                        )
                    )
            except Exception:
                pass
            reason = (
                str(failure)
                if isinstance(failure, MaterialRunOutputError)
                else "CONTROLLED_RESOURCE_INVALID"
            )
            if cleanup_failed:
                reason = "MATERIAL_RUN_STORAGE_FAILED"
            raise MaterialProcessingError(reason) from None
    return read_material_processing_run(learner_id, run_id, dsn=dsn)


def recover_interrupted_material_runs(*, dsn: str | None = None) -> int:
    """啟動時把遺留 running run 標成明確失敗，不自動重跑。"""
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


def claim_next_material_processing_run(*, dsn: str | None = None) -> ClaimedMaterialProcessingRun | None:
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


def _valid_counts(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _PROVIDER_COUNT_KEYS
        and all(type(count) is int and count >= 0 for count in value.values())
        and value["total"] == sum(value[key] for key in _PROVIDER_COUNT_KEYS - {"total"})
    )


def _safe_development_run_status(
    development_run: Any,
    run_id: UUID,
    source_sha256: str,
    runtime_binding_sha256: str,
    page_limit: int,
) -> dict[str, Any] | None:
    """只接受目前 development PDF run 的封閉結果與逐頁 exact outcome。"""
    if (
        not isinstance(development_run, dict)
        or set(development_run) != _DEVELOPMENT_RUN_RESULT_KEYS
    ):
        return None
    if (
        development_run["schema"] != RUN_SCHEMA
        or development_run["development_only"] is not True
        or development_run["run_id"] != str(run_id)
    ):
        return None
    binding = development_run["input_binding"]
    if not isinstance(binding, dict) or set(binding) != {"material_ref", "source_sha256", "page_count", "runtime_binding_sha256"}:
        return None
    page_count = binding["page_count"]
    if (
        binding.get("material_ref") != f"material:sha256:{source_sha256}"
        or binding.get("source_sha256") != source_sha256
        or binding.get("runtime_binding_sha256") != runtime_binding_sha256
        or type(page_count) is not int
        or not 1 <= page_count <= page_limit
        or not _valid_counts(development_run["provider_call_counts"])
        or not _valid_counts(development_run["cache_hits"])
        or not all(
            isinstance(development_run[key], str)
            for key in ("processing", "quality", "decision", "reason_code")
        )
    ):
        return None
    statuses = development_run["page_statuses"]
    non_failed = development_run["processing"] in {"succeeded", "partial"}
    if not isinstance(statuses, list) or len(statuses) > page_count or (non_failed and len(statuses) != page_count):
        return None
    numbers = set()
    status_fields = {"page_number", "page_ref", "last_stage", "processing", "quality", "decision", "reason_code"}
    completed = {
        ("concept", "succeeded", "accepted", "retain", "CONCEPTS_READY"),
        ("concept", "partial", "needs_review", "review", "CONCEPT_CONTEXT_UNAVAILABLE"),
        (
            "page_structure", "failed", "unsupported", "reject",
            "PAGE_STRUCTURE_INVALID",
        ),
        (
            "visual_alignment", "failed", "unsupported", "reject",
            "VISUAL_ALIGNMENT_REVIEW_REJECTED",
        ),
    }
    allowed = {
        ("succeeded", "accepted", "retain"),
        ("failed", "unsupported", "reject"),
        ("partial", "needs_review", "review"),
    }
    for status in statuses:
        if not isinstance(status, dict) or set(status) != status_fields:
            return None
        number = status["page_number"]
        if type(number) is not int or not 1 <= number <= page_count or number in numbers:
            return None
        numbers.add(number)
        expected_ref = "page:sha256:" + sha256(f"{source_sha256}:{number}".encode("ascii")).hexdigest()
        if status["page_ref"] != expected_ref or not all(isinstance(status[key], str) for key in ("last_stage", "processing", "quality", "decision", "reason_code")):
            return None
        outcome = tuple(status[key] for key in ("processing", "quality", "decision"))
        if (
            status["last_stage"] not in {"page_evidence", "page_structure", "visual_alignment", "concept"}
            or outcome not in allowed
            or re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", status["reason_code"]) is None
            or (non_failed and (status["last_stage"], *outcome, status["reason_code"]) not in completed)
        ):
            return None
    if non_failed and numbers != set(range(1, page_count + 1)):
        return None
    outcome = tuple(
        development_run[key]
        for key in ("processing", "quality", "decision", "reason_code")
    )
    if development_run["processing"] == "failed":
        if (
            outcome[:3] != ("failed", "unsupported", "reject")
            or development_run["study_material_output"] is not None
            or re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,99}", development_run["reason_code"]
            )
            is None
        ):
            return None
    elif outcome not in _SAFE_DEVELOPMENT_RUN_OUTCOMES:
        return None
    else:
        output = development_run["study_material_output"]
        if not isinstance(output, dict):
            return None
        limitations = output.get("known_limitations")
        if not isinstance(limitations, list):
            return None
        excluded_statuses = [
            status
            for status in statuses
            if status["processing"] == "failed"
        ]
        exclusion = next(
            (
                item
                for item in limitations
                if isinstance(item, dict)
                and item.get("reason_code") == "PAGE_CONTENT_EXCLUDED"
            ),
            None,
        )
        affected_pages = (
            exclusion.get("affected_pages")
            if isinstance(exclusion, dict)
            else None
        )
        if bool(excluded_statuses) != isinstance(affected_pages, list):
            return None
        if excluded_statuses:
            public_status_fields = {
                "page_ref",
                "page_number",
                "last_stage",
                "processing",
                "quality",
                "decision",
                "reason_code",
            }
            if [
                {
                    key: page[key]
                    for key in public_status_fields
                }
                for page in affected_pages
                if isinstance(page, dict)
                and public_status_fields.issubset(page)
            ] != excluded_statuses:
                return None
    return {
        "processing": outcome[0], "quality": outcome[1], "decision": outcome[2],
        "reason_code": outcome[3],
        "provider_call_counts": deepcopy(development_run["provider_call_counts"]),
    }


def _terminal_failure(run_id: UUID, reason: str, *, dsn: str | None) -> None:
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
                    error_code=reason,
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
    """驗證來源、執行 development PDF pipeline，再發布全部 terminal outputs。"""
    if not isinstance(claim, ClaimedMaterialProcessingRun):
        raise MaterialProcessingError("MATERIAL_RUN_CLAIM_INVALID")
    run = claim.run
    if development_pipeline_binding(local_config) != run.runtime_binding:
        _terminal_failure(run.run_id, "MATERIAL_CONFIGURATION_INVALID", dsn=dsn)
        return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)
    try:
        source_sha256 = _source_hash(run.learner_id, run.material_id, run.source_artifact_id, dsn=dsn)
        with database_session(dsn) as session:
            catalog = session.scalar(
                select(ResourceCatalog.document).where(
                    ResourceCatalog.learner_id == run.learner_id,
                    ResourceCatalog.material_id == run.material_id,
                    ResourceCatalog.catalog_revision == run.catalog_revision,
                )
            )
        if catalog is None:
            raise MaterialProcessingError("CONTROLLED_RESOURCE_INVALID")
        with tempfile.TemporaryDirectory(prefix="studydy-material-run-") as directory:
            private = Path(directory)
            private.chmod(0o700)
            source_path = private / "source.pdf"
            output_root = private / "output"
            output_root.mkdir(mode=0o700)
            with open_verified_source_pdf(run.learner_id, run.source_artifact_id, dsn=dsn) as source:
                with source_path.open("xb") as destination:
                    while chunk := source.file.read(_CHUNK):
                        destination.write(chunk)
            development_run = run_development_pdf(
                source_path, source_sha256, output_root, local_config,
                run_id=str(run.run_id), produced_at=_now().isoformat(), page_limit=run.page_limit,
            )
        safe_run_status = _safe_development_run_status(
            development_run,
            run.run_id,
            source_sha256,
            run.runtime_binding["runtime_binding_sha256"],
            run.page_limit,
        )
        if safe_run_status is None:
            raise MaterialProcessingError("MATERIAL_S1_FAILED")
        if safe_run_status["processing"] == "failed":
            reason = (
                safe_run_status["reason_code"]
                if safe_run_status["reason_code"] in _SAFE_PROVIDER_ERRORS
                else "MATERIAL_S1_FAILED"
            )
            _terminal_failure(run.run_id, reason, dsn=dsn)
            return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)
        output = development_run["study_material_output"]
        if (
            validate_study_material_output(output) is not None
            or output.get("handoff_id") != str(run.run_id)
            or output.get("material_ref") != f"material:sha256:{source_sha256}"
        ):
            raise MaterialProcessingError("MATERIAL_S1_FAILED")
        publish_terminal_outputs(
            run.learner_id, run.material_id, run.run_id, run.subject,
            catalog, output, safe_run_status, dsn=dsn,
        )
    except MaterialProcessingError as error:
        reason = str(error)
        if reason not in {
            "MATERIAL_CONFIGURATION_INVALID", "MATERIAL_S1_FAILED",
            "LOCAL_PROVIDER_TIMEOUT", "LOCAL_PROVIDER_RATE_LIMITED", "LOCAL_PROVIDER_TRANSIENT_ERROR",
            "CONTROLLED_RESOURCE_INVALID", "MATERIAL_OUTPUT_FAILED",
        }:
            reason = "MATERIAL_S1_FAILED"
        _terminal_failure(run.run_id, reason, dsn=dsn)
    except MaterialRunOutputError:
        _terminal_failure(run.run_id, "MATERIAL_OUTPUT_FAILED", dsn=dsn)
    except Exception:
        _terminal_failure(run.run_id, "MATERIAL_S1_FAILED", dsn=dsn)
    return read_material_processing_run(run.learner_id, run.run_id, dsn=dsn)

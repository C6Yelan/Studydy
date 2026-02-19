"""Batch extraction pipeline for T2 document processing."""

from __future__ import annotations

import hashlib
import traceback
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Iterable
from uuid import uuid4

import yaml

from app.services.extraction.base import BaseExtractor
from app.services.extraction.docx import DocxExtractor
from app.services.extraction.models import (
    EXTRACTOR_VERSION,
    DocumentResult,
    ExtractionError,
    FileType,
    utc_now_iso,
)
from app.services.extraction.pdf import PdfExtractor
from app.services.extraction.pptx import PptxExtractor
from app.services.extraction.writer import write_batch_report, write_document_outputs


BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "datasets_local" / "raw"

SUPPORTED_EXTENSIONS: dict[str, FileType] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
}

EXTRACTORS: dict[FileType, BaseExtractor] = {
    "pdf": PdfExtractor(),
    "docx": DocxExtractor(),
    "pptx": PptxExtractor(),
}


def generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{timestamp}-{uuid4().hex[:8]}"


def _log_line(level: str, message: str) -> str:
    return f"{utc_now_iso()} [{level}] {message}"


def _to_repo_relative(path: Path) -> str:
    resolved = path.resolve()
    for root in (REPO_ROOT, BACKEND_ROOT, Path.cwd()):
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def infer_file_type(path: Path) -> FileType:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {path}")
    return SUPPORTED_EXTENSIONS[suffix]


def _resolve_manifest_entry_path(manifest_path: Path, path_text: str) -> Path:
    path_obj = Path(path_text)
    if path_obj.is_absolute():
        return path_obj

    search_roots = [manifest_path.parent, BACKEND_ROOT, REPO_ROOT, Path.cwd()]
    for root in search_roots:
        candidate = (root / path_obj).resolve()
        if candidate.exists():
            return candidate

    return (manifest_path.parent / path_obj).resolve()


def _iter_manifest_files(manifest_path: Path) -> Iterable[Path]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a mapping object: {manifest_path}")

    datasets = data.get("datasets", [])
    if not isinstance(datasets, list):
        raise ValueError(f"Manifest datasets must be a list: {manifest_path}")

    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue
        files = dataset.get("files", [])
        if not isinstance(files, list):
            continue
        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            relative_path = file_entry.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path.strip():
                continue
            resolved_path = _resolve_manifest_entry_path(manifest_path, relative_path.strip())
            if resolved_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield resolved_path


def discover_files(input_path: Path, manifest_path: Path | None = None) -> list[Path]:
    if manifest_path is not None:
        if not manifest_path.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")

        discovered: list[Path] = []
        missing: list[str] = []
        for file_path in _iter_manifest_files(manifest_path):
            if file_path.exists() and file_path.is_file():
                discovered.append(file_path.resolve())
            else:
                missing.append(file_path.as_posix())

        if missing:
            preview = ", ".join(missing[:5])
            raise FileNotFoundError(f"Manifest includes missing files ({len(missing)}): {preview}")

        unique = sorted({path.resolve() for path in discovered})
        return unique

    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")

    if input_path.is_file():
        infer_file_type(input_path)
        return [input_path.resolve()]

    discovered = sorted(
        path.resolve()
        for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    return discovered


def extract_document(file_path: Path, run_id: str, source_path: str | None = None) -> DocumentResult:
    resolved_path = file_path.resolve()
    file_type = infer_file_type(resolved_path)
    extracted_at = utc_now_iso()
    source = source_path or _to_repo_relative(resolved_path)

    log_lines = [
        _log_line("INFO", f"run_id={run_id}"),
        _log_line("INFO", f"source={source}"),
        _log_line("INFO", f"file_type={file_type}"),
    ]

    size_bytes = resolved_path.stat().st_size
    sha256 = _sha256_file(resolved_path)
    doc_uid = sha256

    extractor = EXTRACTORS[file_type]

    try:
        segments, warnings = extractor.extract(
            file_path=resolved_path,
            source_path=source,
            doc_uid=doc_uid,
            extracted_at=extracted_at,
        )
        log_lines.append(_log_line("INFO", f"segments={len(segments)} warnings={len(warnings)}"))
        return DocumentResult(
            source_path=source,
            file_path=resolved_path,
            file_type=file_type,
            doc_uid=doc_uid,
            sha256=sha256,
            size_bytes=size_bytes,
            extracted_at=extracted_at,
            segments=segments,
            warnings=warnings,
            errors=[],
            log_lines=log_lines,
            status="success",
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        stack_trace = traceback.format_exc()
        log_lines.append(_log_line("ERROR", f"extraction failed: {exc}"))
        error = ExtractionError(
            code="extract_failed",
            message="Document extraction failed.",
            exception_type=type(exc).__name__,
            stack_trace=stack_trace,
        )
        return DocumentResult(
            source_path=source,
            file_path=resolved_path,
            file_type=file_type,
            doc_uid=doc_uid,
            sha256=sha256,
            size_bytes=size_bytes,
            extracted_at=extracted_at,
            segments=[],
            warnings=[],
            errors=[error],
            log_lines=log_lines,
            status="failed",
        )


def run_extraction_batch(
    input_path: Path,
    output_path: Path = DEFAULT_OUTPUT_DIR,
    manifest_path: Path | None = None,
    run_id: str | None = None,
    fail_fast: bool = False,
) -> dict[str, object]:
    """Run extraction for discovered input files and write artifacts."""
    batch_start = monotonic()
    started_at = utc_now_iso()

    current_run_id = run_id or generate_run_id()
    output_root = output_path.resolve()

    run_dir = output_root / "runs" / current_run_id
    documents_root = run_dir / "documents"

    if run_dir.exists():
        raise FileExistsError(
            f"Run output already exists for run_id={current_run_id}. "
            "Use a new --run-id to preserve previous raw outputs."
        )

    documents_root.mkdir(parents=True, exist_ok=False)

    files = discover_files(input_path=input_path, manifest_path=manifest_path)

    results: list[DocumentResult] = []
    seen_doc_uids: set[str] = set()

    for file_path in files:
        result = extract_document(file_path=file_path, run_id=current_run_id)

        if result.doc_uid in seen_doc_uids:
            duplicate_error = ExtractionError(
                code="duplicate_doc_uid",
                message=(
                    "Duplicate doc_uid in same run; keeping first document only to avoid raw overwrite."
                ),
            )
            result.status = "failed"
            result.errors.append(duplicate_error)
            result.log_lines.append(_log_line("ERROR", duplicate_error.message))
            results.append(result)
            if fail_fast:
                break
            continue

        seen_doc_uids.add(result.doc_uid)

        try:
            doc_dir = write_document_outputs(output_root=output_root, run_id=current_run_id, result=result)
            result.output_dir = _to_repo_relative(doc_dir)
        except Exception as exc:  # pragma: no cover - defensive fallback
            result.status = "failed"
            result.errors.append(
                ExtractionError(
                    code="write_failed",
                    message="Failed to write extraction artifacts.",
                    exception_type=type(exc).__name__,
                    stack_trace=traceback.format_exc(),
                )
            )
            result.log_lines.append(_log_line("ERROR", f"artifact write failed: {exc}"))

        results.append(result)
        if fail_fast and result.status == "failed":
            break

    finished_at = utc_now_iso()
    duration_seconds = round(monotonic() - batch_start, 3)

    success_count = sum(1 for result in results if result.status == "success")
    failed_count = sum(1 for result in results if result.status == "failed")
    warning_count = sum(len(result.warnings) for result in results)

    report: dict[str, object] = {
        "run_id": current_run_id,
        "extractor_version": EXTRACTOR_VERSION,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration_seconds,
        "input": _to_repo_relative(input_path),
        "manifest": _to_repo_relative(manifest_path) if manifest_path is not None else None,
        "output_root": _to_repo_relative(output_root),
        "totals": {
            "discovered_documents": len(files),
            "processed_documents": len(results),
            "succeeded": success_count,
            "failed": failed_count,
            "warnings": warning_count,
            "stopped_early": bool(fail_fast and len(results) < len(files)),
        },
        "documents": [result.to_report_dict() for result in results],
    }

    report_path = output_root / "reports" / f"{current_run_id}.json"
    report["report_path"] = _to_repo_relative(report_path)
    write_batch_report(output_root=output_root, run_id=current_run_id, report=report)

    return report

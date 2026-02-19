"""Core models for T2 document extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


FileType = Literal["pdf", "docx", "pptx"]
UnitType = Literal["page", "slide", "paragraph"]

EXTRACTOR_VERSION = "t2-v1"


def utc_now_iso() -> str:
    """Return current UTC time in compact ISO8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True, frozen=True)
class ExtractionWarning:
    code: str
    message: str
    locator: dict[str, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.locator:
            payload["locator"] = dict(self.locator)
        return payload


@dataclass(slots=True, frozen=True)
class ExtractionError:
    code: str
    message: str
    exception_type: str | None = None
    stack_trace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.exception_type:
            payload["exception_type"] = self.exception_type
        if self.stack_trace:
            payload["stack_trace"] = self.stack_trace
        return payload


@dataclass(slots=True, frozen=True)
class Segment:
    segment_id: str
    doc_uid: str
    source_path: str
    file_type: FileType
    unit_type: UnitType
    unit_index: int
    text: str
    locator: dict[str, int]
    extracted_at: str
    extractor_version: str = EXTRACTOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "doc_uid": self.doc_uid,
            "source_path": self.source_path,
            "file_type": self.file_type,
            "unit_type": self.unit_type,
            "unit_index": self.unit_index,
            "text": self.text,
            "locator": dict(self.locator),
            "extracted_at": self.extracted_at,
            "extractor_version": self.extractor_version,
        }


@dataclass(slots=True)
class DocumentMeta:
    doc_uid: str
    filename: str
    file_type: FileType
    size_bytes: int
    sha256: str
    extracted_at: str
    run_id: str
    stats: dict[str, int]
    warnings: list[ExtractionWarning] = field(default_factory=list)
    errors: list[ExtractionError] = field(default_factory=list)
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "doc_uid": self.doc_uid,
            "filename": self.filename,
            "file_type": self.file_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "extracted_at": self.extracted_at,
            "run_id": self.run_id,
            "stats": dict(self.stats),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "errors": [error.to_dict() for error in self.errors],
        }
        if self.source_path is not None:
            payload["source_path"] = self.source_path
        return payload


@dataclass(slots=True)
class DocumentResult:
    source_path: str
    file_path: Path
    file_type: FileType
    doc_uid: str
    sha256: str
    size_bytes: int
    extracted_at: str
    segments: list[Segment] = field(default_factory=list)
    warnings: list[ExtractionWarning] = field(default_factory=list)
    errors: list[ExtractionError] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    status: Literal["success", "failed"] = "success"
    output_dir: str | None = None

    def build_meta(self, run_id: str) -> DocumentMeta:
        non_empty_segments = sum(1 for segment in self.segments if segment.text.strip())
        empty_segments = len(self.segments) - non_empty_segments
        return DocumentMeta(
            doc_uid=self.doc_uid,
            filename=self.file_path.name,
            file_type=self.file_type,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
            extracted_at=self.extracted_at,
            run_id=run_id,
            stats={
                "segments": len(self.segments),
                "non_empty_segments": non_empty_segments,
                "empty_segments": empty_segments,
            },
            warnings=self.warnings,
            errors=self.errors,
            source_path=self.source_path,
        )

    def to_report_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_path": self.source_path,
            "doc_uid": self.doc_uid,
            "file_type": self.file_type,
            "status": self.status,
            "segments": len(self.segments),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "errors": [error.to_dict() for error in self.errors],
        }
        if self.output_dir is not None:
            payload["output_dir"] = self.output_dir
        return payload

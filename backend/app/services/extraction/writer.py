"""Writers for extraction artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.extraction.models import DocumentResult


def write_document_outputs(output_root: Path, run_id: str, result: DocumentResult) -> Path:
    """Write raw segments, document metadata, and extraction logs."""
    doc_dir = output_root / "runs" / run_id / "documents" / result.doc_uid
    if doc_dir.exists():
        raise FileExistsError(f"Document output already exists for doc_uid={result.doc_uid} in run_id={run_id}")

    doc_dir.mkdir(parents=True, exist_ok=False)

    raw_path = doc_dir / "raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as file_obj:
        for segment in result.segments:
            file_obj.write(json.dumps(segment.to_dict(), ensure_ascii=False) + "\n")

    meta = result.build_meta(run_id=run_id)
    (doc_dir / "meta.json").write_text(
        json.dumps(meta.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_content = "\n".join(result.log_lines)
    if log_content:
        log_content += "\n"
    (doc_dir / "extract.log").write_text(log_content, encoding="utf-8")

    return doc_dir


def write_batch_report(output_root: Path, run_id: str, report: dict[str, Any]) -> Path:
    """Write batch-level JSON report."""
    reports_dir = output_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_path = reports_dir / f"{run_id}.json"
    if report_path.exists():
        raise FileExistsError(f"Report already exists for run_id={run_id}: {report_path}")

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path

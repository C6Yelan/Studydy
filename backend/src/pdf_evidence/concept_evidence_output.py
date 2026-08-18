"""建立並原子發布 internal Concept/Evidence output 與 truthful terminal。"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any

from .ocr_page_evidence import canonical_bytes, canonical_sha256


OUTPUT_SCHEMA = "concept-evidence-output/v1"
TERMINAL_SCHEMA = "text-first-run-terminal/v1"
KNOWN_REASONS = {
    "MEDIA_TYPE_INVALID",
    "SOURCE_READ_FAILED",
    "SOURCE_HASH_MISMATCH",
    "PDF_INVALID",
    "PDF_ENCRYPTED",
    "PAGE_SELECTION_INVALID",
    "RUNTIME_BINDING_INVALID",
    "PROTOCOL_LIMIT_EXCEEDED",
    "CHILD_TIMEOUT",
    "CHILD_EXITED",
    "CHILD_RESPONSE_INVALID",
    "MODEL_OOM",
    "MODEL_GENERATION_FAILED",
    "MODEL_INPUT_TOO_LARGE",
    "OCR_OUTPUT_INVALID",
    "OCR_LOCATOR_INVALID",
    "NO_USABLE_EVIDENCE",
    "PAGE_CONTENT_REVIEW_REQUIRED",
    "MODEL_OUTPUT_TOO_LARGE",
    "MODEL_OUTPUT_INVALID_JSON",
    "MODEL_OUTPUT_TRUNCATED",
    "CANDIDATE_SCHEMA_INVALID",
    "INVALID_CONCEPT_COUNT",
    "INVALID_TEXT_FIELD",
    "INVALID_KEY_POINTS",
    "INVALID_EVIDENCE_REFERENCES",
    "DUPLICATE_EVIDENCE_REFERENCE",
    "UNKNOWN_EVIDENCE_ID",
    "NO_USABLE_CONCEPT",
    "TRAILING_QUOTE_REMOVED",
    "SEMANTIC_REVIEW_REQUIRED",
    "CACHE_INVALID",
    "CACHE_WRITE_FAILED",
    "ARTIFACT_COLLISION",
    "FINAL_OUTPUT_WRITE_FAILED",
    "RUN_TERMINAL_WRITE_FAILED",
    "INTERNAL_FAILURE",
}


def clean_reasons(reasons: list[str]) -> list[str]:
    return sorted({reason if reason in KNOWN_REASONS else "INTERNAL_FAILURE" for reason in reasons})


def build_output(
    *,
    run_id: str,
    produced_at: str,
    source_binding: dict[str, Any],
    pages: list[dict[str, Any]],
    semantic_pages: list[dict[str, Any]],
    runtime_binding: dict[str, Any],
    run_reasons: list[str],
) -> dict[str, Any]:
    concepts = [concept for page in semantic_pages for concept in page["concepts"]]
    if not concepts:
        raise ValueError("NO_USABLE_CONCEPT")
    rejected = [
        {"page_ref": page["page_ref"], **candidate}
        for page in semantic_pages
        for candidate in page["rejected_candidates"]
    ]
    reasons = run_reasons + ["SEMANTIC_REVIEW_REQUIRED"]
    reasons.extend(reason for page in pages for reason in page["reason_codes"])
    reasons.extend(reason for page in semantic_pages for reason in page["reason_codes"])
    output = {
        "schema": OUTPUT_SCHEMA,
        "run_id": run_id,
        "produced_at": produced_at,
        "material_id": pages[0]["material_id"],
        "material_revision": pages[0]["material_revision"],
        "source_binding": source_binding,
        "analyzed_page_refs": [page["page_ref"] for page in pages],
        "page_evidence_refs": [page["page_evidence_id"] for page in pages],
        "concepts": concepts,
        "rejected_candidates": rejected,
        "runtime_binding": runtime_binding,
        "processing": "partial",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": clean_reasons(reasons),
    }
    output["output_id"] = f"concept-evidence-output:sha256:{canonical_sha256(output)}"
    if len(canonical_bytes(output)) > 4 * 1024 * 1024:
        raise ValueError("PROTOCOL_LIMIT_EXCEEDED")
    return output


def build_terminal(
    *,
    run_id: str,
    produced_at: str,
    output: dict[str, Any] | None,
    runtime_binding_sha256: str,
    reasons: list[str],
    duration_ms: int,
    ocr_calls: int,
    concept_calls: int,
) -> dict[str, Any]:
    failed = output is None
    return {
        "schema": TERMINAL_SCHEMA,
        "run_id": run_id,
        "produced_at": produced_at,
        "output_id": output["output_id"] if output is not None else None,
        "runtime_binding_sha256": runtime_binding_sha256,
        "processing": "failed" if failed else "partial",
        "quality": "needs_review",
        "decision": "reject" if failed else "review",
        "reason_codes": clean_reasons(reasons),
        "duration_ms": duration_ms,
        "ocr_calls": ocr_calls,
        "concept_calls": concept_calls,
    }


def _write_new(path: Path, encoded: bytes) -> None:
    with path.open("xb") as destination:
        destination.write(encoded)
        destination.flush()
        os.fsync(destination.fileno())


def publish_run(
    runtime_root: Path,
    run_id: str,
    output: dict[str, Any] | None,
    terminal: dict[str, Any],
) -> Path:
    """output 重讀一致後才寫 terminal，最後以單次 rename 發布。"""
    runs = runtime_root / "runs"
    if runs.is_symlink() or (runs.exists() and not runs.is_dir()):
        raise OSError("RUN_TERMINAL_WRITE_FAILED")
    runs.mkdir(parents=True, exist_ok=True)
    destination = runs / run_id
    if os.path.lexists(destination):
        raise FileExistsError("ARTIFACT_COLLISION")
    stage = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
    try:
        if output is not None:
            encoded_output = canonical_bytes(output)
            output_path = stage / "concept-evidence-output.json"
            try:
                _write_new(output_path, encoded_output)
                if output_path.read_bytes() != encoded_output:
                    raise OSError
            except OSError as error:
                raise OSError("FINAL_OUTPUT_WRITE_FAILED") from error
        encoded_terminal = canonical_bytes(terminal)
        terminal_path = stage / "terminal.json"
        try:
            _write_new(terminal_path, encoded_terminal)
            if terminal_path.read_bytes() != encoded_terminal:
                raise OSError
            directory_descriptor = os.open(runs, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
                os.replace(stage, destination)
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as error:
            raise OSError("RUN_TERMINAL_WRITE_FAILED") from error
        return destination
    finally:
        if stage.exists():
            for child in stage.iterdir():
                child.unlink(missing_ok=True)
            stage.rmdir()

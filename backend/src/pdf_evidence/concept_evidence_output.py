"""建立、發布並重驗本機文字優先流程的封閉產物。"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .ocr_page_evidence import canonical_bytes, canonical_sha256


OUTPUT_SCHEMA = "concept-evidence-output/v2"
TERMINAL_SCHEMA = "text-first-run-terminal/v2"
BUNDLE_SCHEMA = "text-first-producer-bundle/v1"
AGGREGATION_POLICY = "whole-document-review-aggregation/v1"
MAX_BUNDLE_FILE_BYTES = 16 * 1024 * 1024
KNOWN_REASONS = {
    "MEDIA_TYPE_INVALID",
    "SOURCE_READ_FAILED",
    "SOURCE_HASH_MISMATCH",
    "PDF_INVALID",
    "PDF_ENCRYPTED",
    "PAGE_SELECTION_INVALID",
    "MATERIAL_PAGE_LIMIT_EXCEEDED",
    "RUNTIME_BINDING_INVALID",
    "RUNTIME_BUSY",
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
    "PAGE_CONTENT_EXCLUDED",
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
    "PRODUCER_BUNDLE_INVALID",
    "INTERNAL_FAILURE",
}


def clean_reasons(reasons: list[str]) -> list[str]:
    return sorted(
        {reason if reason in KNOWN_REASONS else "INTERNAL_FAILURE" for reason in reasons}
    )


def _page_number(page_ref: str, pages: dict[str, dict[str, Any]]) -> int:
    page = pages.get(page_ref)
    if page is None or type(page.get("page_number")) is not int:
        raise ValueError("UNKNOWN_EVIDENCE_ID")
    return page["page_number"]


def _validate_page_links(
    page_artifacts: list[dict[str, Any]], semantic_pages: list[dict[str, Any]]
) -> None:
    """Concept 與 image-lite Evidence 必須留在自己的 PDF 頁面。"""

    pages: dict[str, dict[str, Any]] = {}
    evidence_pages: dict[str, str] = {}
    for page in page_artifacts:
        page_ref = page.get("page_ref")
        if not isinstance(page_ref, str) or page_ref in pages:
            raise ValueError("OCR_LOCATOR_INVALID")
        if page.get("coordinate_space") != "unrotated_pdf_points":
            raise ValueError("OCR_LOCATOR_INVALID")
        pages[page_ref] = page
        for block in page.get("evidence_blocks", []):
            evidence_id = block.get("evidence_id") if isinstance(block, dict) else None
            locator = block.get("locator") if isinstance(block, dict) else None
            if (
                not isinstance(evidence_id, str)
                or evidence_id in evidence_pages
                or not isinstance(locator, dict)
                or locator.get("page") != page.get("page_number")
            ):
                raise ValueError("OCR_LOCATOR_INVALID")
            evidence_pages[evidence_id] = page_ref
        for image in page.get("images", []):
            if not isinstance(image, dict):
                raise ValueError("OCR_LOCATOR_INVALID")
            references = image.get("caption_evidence_ids", []) + image.get(
                "nearby_evidence_ids", []
            )
            if any(evidence_pages.get(item) != page_ref for item in references):
                raise ValueError("UNKNOWN_EVIDENCE_ID")

    semantic_refs: set[str] = set()
    for semantic_page in semantic_pages:
        page_ref = semantic_page.get("page_ref")
        if not isinstance(page_ref, str) or page_ref in semantic_refs or page_ref not in pages:
            raise ValueError("UNKNOWN_EVIDENCE_ID")
        semantic_refs.add(page_ref)
        for concept in semantic_page.get("concepts", []):
            if concept.get("page_ref") != page_ref or any(
                evidence_pages.get(evidence_id) != page_ref
                for evidence_id in concept.get("evidence_ids", [])
            ):
                raise ValueError("UNKNOWN_EVIDENCE_ID")
    if semantic_refs != set(pages):
        raise ValueError("NO_USABLE_CONCEPT")


def build_output(
    *,
    run_id: str,
    produced_at: str,
    source_binding: dict[str, Any],
    pages: list[dict[str, Any]],
    semantic_pages: list[dict[str, Any]],
    runtime_binding: dict[str, Any],
    run_reasons: list[str],
    excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    excluded = deepcopy(excluded_pages or [])
    if not pages or not semantic_pages:
        raise ValueError("NO_USABLE_CONCEPT")
    _validate_page_links(pages, semantic_pages)

    concepts = []
    for semantic_page in semantic_pages:
        for source_concept in semantic_page["concepts"]:
            concept = deepcopy(source_concept)
            concept["processing"] = "succeeded"
            concept["quality"] = "needs_review"
            concept["decision"] = "review"
            concepts.append(concept)
    if not concepts:
        raise ValueError("NO_USABLE_CONCEPT")
    concepts.sort(key=lambda concept: (concept["page_ref"], concept["concept_id"]))

    rejected = [
        {"page_ref": page["page_ref"], **deepcopy(candidate)}
        for page in semantic_pages
        for candidate in page["rejected_candidates"]
    ]
    reasons = run_reasons + ["SEMANTIC_REVIEW_REQUIRED"]
    reasons.extend(reason for page in pages for reason in page["reason_codes"])
    reasons.extend(reason for page in semantic_pages for reason in page["reason_codes"])
    if excluded:
        reasons.append("PAGE_CONTENT_EXCLUDED")
    output = {
        "schema": OUTPUT_SCHEMA,
        "aggregation_policy": AGGREGATION_POLICY,
        "run_id": run_id,
        "produced_at": produced_at,
        "material_id": pages[0]["material_id"],
        "material_revision": pages[0]["material_revision"],
        "source_binding": deepcopy(source_binding),
        "pages": deepcopy(pages),
        "excluded_pages": excluded,
        "concepts": concepts,
        "rejected_candidates": rejected,
        "runtime_binding": deepcopy(runtime_binding),
        "processing": "partial" if excluded else "succeeded",
        "quality": "needs_review",
        "decision": "review",
        "reason_codes": clean_reasons(reasons),
    }
    output["output_id"] = f"concept-evidence-output:sha256:{canonical_sha256(output)}"
    if len(canonical_bytes(output)) > MAX_BUNDLE_FILE_BYTES:
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
    ocr_loads: int = 0,
    concept_loads: int = 0,
    page_count: int = 0,
    excluded_pages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    failed = output is None
    excluded_count = (
        len(output.get("excluded_pages", []))
        if output is not None
        else len(excluded_pages or [])
    )
    included_count = len(output.get("pages", [])) if output is not None else 0
    processing = "failed" if failed else output["processing"]
    return {
        "schema": TERMINAL_SCHEMA,
        "aggregation_policy": AGGREGATION_POLICY,
        "run_id": run_id,
        "produced_at": produced_at,
        "output_id": output["output_id"] if output is not None else None,
        "runtime_binding_sha256": runtime_binding_sha256,
        "page_count": page_count or included_count + excluded_count,
        "included_page_count": included_count,
        "excluded_page_count": excluded_count,
        "processing": processing,
        "quality": "needs_review",
        "decision": "reject" if failed else "review",
        "reason_codes": clean_reasons(reasons),
        "duration_ms": duration_ms,
        "ocr_calls": ocr_calls,
        "concept_calls": concept_calls,
        "ocr_loads": ocr_loads,
        "concept_loads": concept_loads,
    }


def _write_new(path: Path, encoded: bytes) -> None:
    with path.open("xb") as destination:
        destination.write(encoded)
        destination.flush()
        os.fsync(destination.fileno())


def _bundle_document(
    run_id: str, output: dict[str, Any] | None, terminal: dict[str, Any]
) -> dict[str, Any]:
    output_bytes = canonical_bytes(output) if output is not None else None
    terminal_bytes = canonical_bytes(terminal)
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "run_id": run_id,
        "output_file": "concept-evidence-output.json" if output is not None else None,
        "output_sha256": canonical_sha256(output) if output is not None else None,
        "terminal_file": "terminal.json",
        "terminal_sha256": canonical_sha256(terminal),
        "output_size_bytes": len(output_bytes) if output_bytes is not None else 0,
        "terminal_size_bytes": len(terminal_bytes),
        "processing": terminal["processing"],
        "quality": terminal.get("quality", "needs_review"),
        "decision": terminal.get("decision", "review"),
    }
    bundle["bundle_id"] = f"text-first-producer-bundle:sha256:{canonical_sha256(bundle)}"
    return bundle


def publish_run(
    runtime_root: Path,
    run_id: str,
    output: dict[str, Any] | None,
    terminal: dict[str, Any],
) -> Path:
    """全部檔案重讀一致後，才以一次 rename 發布不可覆寫的 bundle。"""

    if (
        runtime_root.is_symlink()
        or not isinstance(run_id, str)
        or not run_id
        or "/" in run_id
        or "\\" in run_id
    ):
        raise OSError("RUN_TERMINAL_WRITE_FAILED")
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
        encoded_bundle = canonical_bytes(_bundle_document(run_id, output, terminal))
        terminal_path = stage / "terminal.json"
        bundle_path = stage / "producer-bundle.json"
        try:
            _write_new(terminal_path, encoded_terminal)
            _write_new(bundle_path, encoded_bundle)
            if (
                terminal_path.read_bytes() != encoded_terminal
                or bundle_path.read_bytes() != encoded_bundle
            ):
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


def _read_json_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BUNDLE_FILE_BYTES:
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, RecursionError, UnicodeDecodeError, ValueError):
        raise ValueError("PRODUCER_BUNDLE_INVALID") from None
    if not isinstance(value, dict):
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    return value


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("PRODUCER_BUNDLE_INVALID")
        value[key] = item
    return value


def read_producer_bundle(runtime_root: Path, run_id: str) -> dict[str, Any]:
    """只從固定檔名讀取，並重驗 bundle、terminal 與 output 的 exact binding。"""

    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    runs = runtime_root / "runs"
    directory = runs / run_id
    if (
        runtime_root.is_symlink()
        or runs.is_symlink()
        or directory.is_symlink()
        or not directory.is_dir()
    ):
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    bundle = _read_json_file(directory / "producer-bundle.json")
    terminal = _read_json_file(directory / "terminal.json")
    expected_files = {"producer-bundle.json", "terminal.json"}
    output: dict[str, Any] | None = None
    if bundle.get("output_file") is not None:
        if bundle.get("output_file") != "concept-evidence-output.json":
            raise ValueError("PRODUCER_BUNDLE_INVALID")
        output = _read_json_file(directory / "concept-evidence-output.json")
        expected_files.add("concept-evidence-output.json")
    try:
        if (
            {item.name for item in directory.iterdir()} != expected_files
            or bundle != _bundle_document(run_id, output, terminal)
            or terminal.get("schema") != TERMINAL_SCHEMA
            or terminal.get("run_id") != run_id
            or terminal.get("output_id")
            != (output.get("output_id") if output is not None else None)
            or (output is not None and output.get("schema") != OUTPUT_SCHEMA)
            or (output is not None and output.get("run_id") != run_id)
        ):
            raise ValueError("PRODUCER_BUNDLE_INVALID")
    except (KeyError, OSError, TypeError):
        raise ValueError("PRODUCER_BUNDLE_INVALID") from None
    return {"bundle": bundle, "terminal": terminal, "output": output}

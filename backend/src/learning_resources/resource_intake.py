from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import resource
import shutil
import tempfile
from typing import Any, Mapping

import pymupdf

from pdf_evidence.artifact_reason_codes import reason_codes_are_valid
from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from pdf_evidence.text_first_bundle import read_producer_bundle
from pdf_evidence.text_first_run import run_full_text_first_pdf
from runtime.local_app import read_local_ai_config_from_environment
from runtime.material_processing import formal_runtime_preflight

from .map_resources import build_resource_library, validate_resource_library


METADATA_SCHEMA = "resource-source-metadata/v1"
CANDIDATE_SCHEMA = "resource-intake-candidate/v1"
CANDIDATE_POLICY = "resource-intake-exact-batch/v1"
REVIEW_REASON = "RESOURCE_HUMAN_REVIEW_REQUIRED"
MAX_JSON_BYTES = 1024 * 1024
MAX_QUOTE_LENGTH = 2_000

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LIBRARY_FILE = Path(__file__).with_name("data") / "resource_library_v1.json"
CANDIDATE_ROOT = REPOSITORY_ROOT / ".studydy-runtime" / "resource-intake" / "candidates"

_METADATA_FIELDS = {
    "schema", "title", "authors", "source_url", "citation", "license",
    "license_url", "use_boundary",
}
_CANDIDATE_FIELDS = {
    "schema", "candidate_policy", "candidate_id", "candidate_content_sha256",
    "source", "base", "runtime", "ceilings", "producer",
    "publishable_proposals", "omitted_items", "critical_blockers",
    "processing", "quality", "decision", "reason_codes", "telemetry",
}
_SOURCE_FIELDS = {"source_sha256", "page_count", "metadata"}
_BASE_FIELDS = {"library_revision", "library_sha256"}
_RUNTIME_FIELDS = {
    "runtime_binding_sha256", "producer_runtime_binding_sha256", "model_id",
    "model_revision", "prompt_sha256", "page_schema", "render_sha256",
}
_CEILING_FIELDS = {"page_ceiling", "latency_ceiling_seconds"}
_PRODUCER_FIELDS = {
    "run_id", "bundle_id", "output_id", "output_sha256",
    "runtime_binding_sha256", "processing", "quality", "decision",
    "reason_codes", "duration_ms", "ocr_calls", "concept_calls",
    "ocr_loads", "concept_loads",
}
_PROPOSAL_FIELDS = {
    "proposal_id", "source_concept_id", "source_evidence_id", "page_number",
    "label", "quote", "region", "processing", "quality", "decision",
    "reason_codes",
}
_REGION_FIELDS = {"coordinate_space", "bbox"}
_OMITTED_FIELDS = {
    "concept_id", "page_number", "reason_code", "processing", "quality",
    "decision",
}
_TELEMETRY_FIELDS = {
    "external_network_calls", "monetary_cost", "peak_rss_kib",
    "peak_vram_bytes",
}
_OMITTED_REASONS = {
    "RESOURCE_EVIDENCE_MISSING",
    "RESOURCE_EVIDENCE_WRONG_PAGE",
    "RESOURCE_EVIDENCE_NOT_GROUNDED",
    "RESOURCE_MULTIPLE_EVIDENCE_NOT_SUPPORTED",
    "RESOURCE_PROPOSAL_UNCERTAIN",
}


class ResourceIntakeError(ValueError):
    """只攜帶可安全輸出的固定 reason code。"""


def _fail(reason: str) -> None:
    raise ResourceIntakeError(reason)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError
        document[key] = value
    return document


def _read_json(path: Path, reason: str) -> tuple[dict[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
            _fail(reason)
        encoded = path.read_bytes()
        document = json.loads(
            encoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except ResourceIntakeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        _fail(reason)
    if not isinstance(document, dict):
        _fail(reason)
    return document, encoded


def _nonempty_text(value: Any, maximum: int = 2_000) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _validate_metadata(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _METADATA_FIELDS:
        _fail("RESOURCE_METADATA_INVALID")
    authors = document["authors"]
    text_fields = _METADATA_FIELDS - {"schema", "authors"}
    if (
        document["schema"] != METADATA_SCHEMA
        or not isinstance(authors, list)
        or not authors
        or authors != list(dict.fromkeys(authors))
        or any(not _nonempty_text(author, 300) for author in authors)
        or any(not _nonempty_text(document[field]) for field in text_fields)
    ):
        _fail("RESOURCE_METADATA_INVALID")
    return deepcopy(document)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _fail("RESOURCE_SOURCE_INVALID")
    return digest.hexdigest()


def _inspect_pdf(path: Path) -> tuple[str, int]:
    try:
        before = path.stat()
    except OSError:
        _fail("RESOURCE_SOURCE_INVALID")
    source_sha256 = _file_sha256(path)
    try:
        with pymupdf.open(path) as document:
            if not document.is_pdf or document.page_count < 1:
                _fail("RESOURCE_SOURCE_INVALID")
            page_count = document.page_count
        after = path.stat()
    except ResourceIntakeError:
        raise
    except Exception:
        _fail("RESOURCE_SOURCE_INVALID")
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        _fail("RESOURCE_SOURCE_INVALID")
    return source_sha256, page_count


def _read_library(path: Path) -> tuple[dict[str, Any], bytes, str]:
    document, encoded = _read_json(path, "RESOURCE_LIBRARY_INVALID")
    if validate_resource_library(document) is not None or canonical_bytes(document) != encoded:
        _fail("RESOURCE_LIBRARY_INVALID")
    return document, encoded, hashlib.sha256(encoded).hexdigest()


def _runtime_summary(local_config: dict[str, Any]) -> dict[str, Any]:
    try:
        binding = formal_runtime_preflight(local_config)
        runtime_lock = local_config["runtime_lock"]
        return {
            "runtime_binding_sha256": binding["runtime_binding_sha256"],
            "producer_runtime_binding_sha256": canonical_sha256(runtime_lock),
            "model_id": runtime_lock["semantic"]["model_id"],
            "model_revision": runtime_lock["semantic"]["revision"],
            "prompt_sha256": runtime_lock["semantic"]["prompt_sha256"],
            "page_schema": runtime_lock["page"]["schema"],
            "render_sha256": runtime_lock["page"]["render_sha256"],
        }
    except ResourceIntakeError:
        raise
    except Exception:
        _fail("RESOURCE_RUNTIME_BINDING_MISMATCH")


def _candidate_identity(
    source: dict[str, Any],
    base: dict[str, Any],
    runtime: dict[str, Any],
    ceilings: dict[str, Any],
    telemetry: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_policy": CANDIDATE_POLICY,
        "source": source,
        "base": base,
        "runtime": runtime,
        "ceilings": ceilings,
        "telemetry": {
            "external_network_calls": telemetry["external_network_calls"],
            "monetary_cost": telemetry["monetary_cost"],
            "peak_vram_bytes": telemetry["peak_vram_bytes"],
        },
    }


def _candidate_id(identity: dict[str, Any]) -> str:
    return "resource-intake-candidate:sha256:" + canonical_sha256(identity)


def _candidate_paths(candidate_id: str) -> tuple[Path, Path, Path]:
    if re.fullmatch(r"resource-intake-candidate:sha256:[0-9a-f]{64}", candidate_id) is None:
        _fail("RESOURCE_CANDIDATE_INVALID")
    directory = CANDIDATE_ROOT / candidate_id
    return directory, directory / "candidate.json", directory / "review.md"


def _valid_bbox(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(type(number) in {int, float} and math.isfinite(number) for number in value)
        and value[0] < value[2]
        and value[1] < value[3]
    )


def _project_output(output: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    pages_by_ref = {page["page_ref"]: page for page in output["pages"]}
    evidence_by_id = {
        block["evidence_id"]: (page, block)
        for page in output["pages"]
        for block in page["evidence_blocks"]
    }
    proposals = []
    omitted = []
    blockers: set[str] = set()
    for concept in output["concepts"]:
        reason = None
        references = concept.get("evidence_ids")
        if not isinstance(references, list) or not references:
            reason = "RESOURCE_EVIDENCE_MISSING"
        elif len(references) != 1:
            reason = "RESOURCE_MULTIPLE_EVIDENCE_NOT_SUPPORTED"
        elif references[0] not in evidence_by_id:
            reason = "RESOURCE_EVIDENCE_MISSING"
        else:
            page, block = evidence_by_id[references[0]]
            locator = block.get("locator")
            quote = block.get("text")
            if page["page_ref"] != concept.get("page_ref"):
                reason = "RESOURCE_EVIDENCE_WRONG_PAGE"
            elif (
                page.get("coordinate_space") != "unrotated_pdf_points"
                or not isinstance(locator, dict)
                or locator.get("page") != page.get("page_number")
                or not _valid_bbox(locator.get("region"))
                or not _nonempty_text(quote, MAX_QUOTE_LENGTH)
                or not _nonempty_text(concept.get("label"), 120)
            ):
                reason = "RESOURCE_EVIDENCE_NOT_GROUNDED"
            elif concept.get("processing") != "succeeded":
                reason = "RESOURCE_PROPOSAL_UNCERTAIN"
        if reason is not None:
            if reason in {
                "RESOURCE_EVIDENCE_MISSING",
                "RESOURCE_EVIDENCE_WRONG_PAGE",
                "RESOURCE_EVIDENCE_NOT_GROUNDED",
            }:
                blockers.add(reason)
            page_number = pages_by_ref.get(concept.get("page_ref"), {}).get("page_number")
            omitted.append({
                "concept_id": concept.get("concept_id"),
                "page_number": page_number,
                "reason_code": reason,
                "processing": "failed",
                "quality": "needs_review",
                "decision": "reject",
            })
            continue
        page, block = evidence_by_id[references[0]]
        proposal_identity = {
            "source_concept_id": concept["concept_id"],
            "source_evidence_id": references[0],
            "page_number": page["page_number"],
            "label": concept["label"],
            "quote": block["text"],
            "region": {
                "coordinate_space": "unrotated_pdf_points",
                "bbox": block["locator"]["region"],
            },
        }
        proposals.append({
            "proposal_id": "resource-proposal:sha256:" + canonical_sha256(proposal_identity),
            **proposal_identity,
            "processing": "partial",
            "quality": "needs_review",
            "decision": "review",
            "reason_codes": [REVIEW_REASON],
        })
    for rejected in output["rejected_candidates"]:
        page = pages_by_ref[rejected["page_ref"]]
        omitted.append({
            "concept_id": (
                f"rejected-candidate:{rejected['page_ref']}:{rejected['candidate_index']}"
            ),
            "page_number": page["page_number"],
            "reason_code": "RESOURCE_PROPOSAL_UNCERTAIN",
            "processing": "failed",
            "quality": "needs_review",
            "decision": "reject",
        })
    proposals.sort(key=lambda item: (item["page_number"], item["proposal_id"]))
    omitted.sort(key=lambda item: (item["page_number"] or 0, str(item["concept_id"])))
    return proposals, omitted, sorted(blockers)


def _producer_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": bundle["run_id"],
        "bundle_id": bundle["bundle_id"],
        "output_id": bundle["output_id"],
        "output_sha256": bundle["output_sha256"],
        "runtime_binding_sha256": bundle["runtime_binding_sha256"],
        "processing": bundle["processing"],
        "quality": bundle["quality"],
        "decision": bundle["decision"],
        "reason_codes": bundle["reason_codes"],
        "duration_ms": bundle["duration_ms"],
        "ocr_calls": bundle["ocr_calls"],
        "concept_calls": bundle["concept_calls"],
        "ocr_loads": bundle["ocr_loads"],
        "concept_loads": bundle["concept_loads"],
    }


def _candidate_bytes(candidate: dict[str, Any]) -> bytes:
    return json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _candidate_content_sha256(candidate: dict[str, Any]) -> str:
    content = dict(candidate)
    content.pop("candidate_content_sha256", None)
    return canonical_sha256(content)


def _review_text(candidate: dict[str, Any], candidate_sha256: str) -> str:
    source = candidate["source"]
    lines = [
        "# Resource intake review", "", f"Candidate ID: `{candidate['candidate_id']}`",
        f"Candidate SHA-256: `{candidate_sha256}`", f"Source SHA-256: `{source['source_sha256']}`",
        f"Physical pages: {source['page_count']}", f"Title: {source['metadata']['title']}",
        f"Authors: {', '.join(source['metadata']['authors'])}",
        f"License: {source['metadata']['license']}", f"Source URL: {source['metadata']['source_url']}",
        f"Base revision: `{candidate['base']['library_revision']}`",
    ]
    runtime = candidate["runtime"]
    producer = candidate["producer"]
    lines.extend([
        f"Runtime binding: `{runtime['runtime_binding_sha256']}`",
        f"Producer runtime binding: `{runtime['producer_runtime_binding_sha256']}`",
        f"Model: `{runtime['model_id']}` @ `{runtime['model_revision']}`",
        f"Prompt SHA-256: `{runtime['prompt_sha256']}`",
        f"Page schema: `{runtime['page_schema']}`",
        f"Render SHA-256: `{runtime['render_sha256']}`",
        f"Producer run: `{producer['run_id']}`",
        f"Producer bundle: `{producer['bundle_id']}`",
        f"Calls: OCR {producer['ocr_calls']}, Qwen {producer['concept_calls']}",
        f"Model loads: OCR {producer['ocr_loads']}, Qwen {producer['concept_loads']}",
        f"Producer duration: {producer['duration_ms']} ms",
        "External network calls: 0",
        "Monetary cost: 0",
        "", "## Publishable proposals", "",
    ])
    for proposal in candidate["publishable_proposals"]:
        lines.extend([
            f"### {proposal['proposal_id']}", f"- Page: {proposal['page_number']}",
            f"- Concept: `{proposal['source_concept_id']}`", f"- Evidence: `{proposal['source_evidence_id']}`",
            f"- Label: {proposal['label']}", f"- Quote: {proposal['quote']}",
            f"- BBox: {proposal['region']['bbox']}", "",
        ])
    lines.extend(["## Omitted items", ""])
    for item in candidate["omitted_items"]:
        lines.append(f"- `{item['concept_id']}`: {item['reason_code']}")
    if not candidate["omitted_items"]:
        lines.append("- None")
    lines.extend(["", "## Critical blockers", ""])
    lines.extend(f"- {reason}" for reason in candidate["critical_blockers"])
    if not candidate["critical_blockers"]:
        lines.append("- None")
    lines.extend([
        "", "## Publish", "",
        "Do not publish if any page, Evidence, label, license, or attribution is wrong.",
        "V1 has no partial acceptance or correction.", "",
        "```text",
        f"python -m learning_resources.resource_intake publish {candidate['candidate_id']} \\",
        f"  --candidate-sha256 {candidate_sha256} \\",
        f"  --confirm {candidate['candidate_id']} \\",
        "  --source-pdf <PDF>", "```", "",
    ])
    return "\n".join(lines)


def _write_candidate(directory: Path, encoded: bytes, review: str) -> None:
    CANDIDATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CANDIDATE_ROOT, 0o700)
    if directory.exists():
        _fail("RESOURCE_CANDIDATE_COLLISION")
    stage = Path(tempfile.mkdtemp(prefix="candidate-", dir=CANDIDATE_ROOT))
    try:
        for name, content in (("candidate.json", encoded), ("review.md", review.encode("utf-8"))):
            with (stage / name).open("xb") as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(stage / name, 0o600)
        os.replace(stage, directory)
    except OSError:
        _fail("RESOURCE_CANDIDATE_WRITE_FAILED")
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _safe_paths(candidate_id: str) -> tuple[str, str]:
    base = f".studydy-runtime/resource-intake/candidates/{candidate_id}"
    return f"{base}/candidate.json", f"{base}/review.md"


def _candidate_is_valid(
    candidate: Any,
    candidate_id: str,
    encoded: bytes,
) -> bool:
    """在 candidate 檔案邊界完整重驗目前 v1 文件。"""

    try:
        if not isinstance(candidate, dict) or set(candidate) != _CANDIDATE_FIELDS:
            return False
        if (
            candidate["schema"] != CANDIDATE_SCHEMA
            or candidate["candidate_policy"] != CANDIDATE_POLICY
            or candidate["candidate_id"] != candidate_id
            or not _is_sha256(candidate["candidate_content_sha256"])
            or candidate["candidate_content_sha256"]
            != _candidate_content_sha256(candidate)
            or candidate["processing"] != "partial"
            or candidate["quality"] != "needs_review"
            or candidate["decision"] != "review"
            or candidate["reason_codes"] != [REVIEW_REASON]
            or candidate["critical_blockers"] != []
            or encoded != _candidate_bytes(candidate)
        ):
            return False

        source = candidate["source"]
        if not isinstance(source, dict) or set(source) != _SOURCE_FIELDS:
            return False
        if (
            not _is_sha256(source["source_sha256"])
            or type(source["page_count"]) is not int
            or source["page_count"] < 1
        ):
            return False
        _validate_metadata(source["metadata"])

        base = candidate["base"]
        if not isinstance(base, dict) or set(base) != _BASE_FIELDS:
            return False
        if (
            not isinstance(base["library_revision"], str)
            or re.fullmatch(
                r"resource-library:sha256:[0-9a-f]{64}",
                base["library_revision"],
            ) is None
            or not _is_sha256(base["library_sha256"])
        ):
            return False

        runtime = candidate["runtime"]
        if not isinstance(runtime, dict) or set(runtime) != _RUNTIME_FIELDS:
            return False
        if (
            not _is_sha256(runtime["runtime_binding_sha256"])
            or not _is_sha256(runtime["producer_runtime_binding_sha256"])
            or not _is_sha256(runtime["prompt_sha256"])
            or not _is_sha256(runtime["render_sha256"])
            or not _nonempty_text(runtime["model_id"])
            or not _nonempty_text(runtime["model_revision"])
            or not _nonempty_text(runtime["page_schema"])
        ):
            return False

        ceilings = candidate["ceilings"]
        if not isinstance(ceilings, dict) or set(ceilings) != _CEILING_FIELDS:
            return False
        if (
            type(ceilings["page_ceiling"]) is not int
            or ceilings["page_ceiling"] < source["page_count"]
            or type(ceilings["latency_ceiling_seconds"]) is not int
            or ceilings["latency_ceiling_seconds"] < 1
        ):
            return False

        telemetry = candidate["telemetry"]
        if not isinstance(telemetry, dict) or set(telemetry) != _TELEMETRY_FIELDS:
            return False
        if (
            type(telemetry["external_network_calls"]) is not int
            or telemetry["external_network_calls"] != 0
            or type(telemetry["monetary_cost"]) is not int
            or telemetry["monetary_cost"] != 0
            or type(telemetry["peak_rss_kib"]) is not int
            or telemetry["peak_rss_kib"] < 0
            or telemetry["peak_vram_bytes"] != "unavailable"
        ):
            return False
        if candidate_id != _candidate_id(
            _candidate_identity(source, base, runtime, ceilings, telemetry)
        ):
            return False

        producer = candidate["producer"]
        if not isinstance(producer, dict) or set(producer) != _PRODUCER_FIELDS:
            return False
        if (
            not isinstance(producer["run_id"], str)
            or re.fullmatch(
                r"text-first-run:[0-9a-fA-F-]{36}", producer["run_id"]
            ) is None
            or not isinstance(producer["bundle_id"], str)
            or re.fullmatch(
                r"text-first-producer-bundle:sha256:[0-9a-f]{64}",
                producer["bundle_id"],
            ) is None
            or not isinstance(producer["output_id"], str)
            or re.fullmatch(
                r"concept-evidence-output:sha256:[0-9a-f]{64}",
                producer["output_id"],
            ) is None
            or not _is_sha256(producer["output_sha256"])
            or producer["runtime_binding_sha256"]
            != runtime["producer_runtime_binding_sha256"]
            or producer["processing"] not in {"succeeded", "partial"}
            or producer["quality"] != "needs_review"
            or producer["decision"] != "review"
            or not reason_codes_are_valid(producer["reason_codes"], formal=True)
            or producer["reason_codes"] != sorted(set(producer["reason_codes"]))
        ):
            return False
        numeric_limits = (
            ("duration_ms", ceilings["latency_ceiling_seconds"] * 1000),
            ("ocr_calls", source["page_count"]),
            ("concept_calls", 2 * source["page_count"]),
            ("ocr_loads", 1),
            ("concept_loads", 1),
        )
        if any(
            type(producer[field]) is not int or not 0 <= producer[field] <= maximum
            for field, maximum in numeric_limits
        ):
            return False

        proposals = candidate["publishable_proposals"]
        if not isinstance(proposals, list) or not proposals:
            return False
        proposal_ids: set[str] = set()
        for proposal in proposals:
            if not isinstance(proposal, dict) or set(proposal) != _PROPOSAL_FIELDS:
                return False
            region = proposal["region"]
            if not isinstance(region, dict) or set(region) != _REGION_FIELDS:
                return False
            if (
                re.fullmatch(
                    r"concept:sha256:[0-9a-f]{64}",
                    proposal["source_concept_id"],
                ) is None
                or re.fullmatch(
                    r"evidence:sha256:[0-9a-f]{64}",
                    proposal["source_evidence_id"],
                ) is None
                or type(proposal["page_number"]) is not int
                or not 1 <= proposal["page_number"] <= source["page_count"]
                or not _nonempty_text(proposal["label"], 120)
                or not _nonempty_text(proposal["quote"], MAX_QUOTE_LENGTH)
                or region["coordinate_space"] != "unrotated_pdf_points"
                or not _valid_bbox(region["bbox"])
                or proposal["processing"] != "partial"
                or proposal["quality"] != "needs_review"
                or proposal["decision"] != "review"
                or proposal["reason_codes"] != [REVIEW_REASON]
            ):
                return False
            proposal_identity = {
                key: proposal[key]
                for key in (
                    "source_concept_id", "source_evidence_id", "page_number",
                    "label", "quote", "region",
                )
            }
            expected_id = (
                "resource-proposal:sha256:" + canonical_sha256(proposal_identity)
            )
            if proposal["proposal_id"] != expected_id or expected_id in proposal_ids:
                return False
            proposal_ids.add(expected_id)
        if proposals != sorted(
            proposals, key=lambda item: (item["page_number"], item["proposal_id"])
        ):
            return False

        omitted_items = candidate["omitted_items"]
        if not isinstance(omitted_items, list):
            return False
        omitted_keys: set[tuple[str, int]] = set()
        for item in omitted_items:
            if not isinstance(item, dict) or set(item) != _OMITTED_FIELDS:
                return False
            if (
                not _nonempty_text(item["concept_id"])
                or type(item["page_number"]) is not int
                or not 1 <= item["page_number"] <= source["page_count"]
                or item["reason_code"] not in _OMITTED_REASONS
                or item["processing"] != "failed"
                or item["quality"] != "needs_review"
                or item["decision"] != "reject"
            ):
                return False
            key = (item["concept_id"], item["page_number"])
            if key in omitted_keys:
                return False
            omitted_keys.add(key)
        if omitted_items != sorted(
            omitted_items,
            key=lambda item: (item["page_number"], item["concept_id"]),
        ):
            return False
        return True
    except (KeyError, RecursionError, ResourceIntakeError, TypeError, ValueError):
        return False


def _load_candidate(candidate_id: str, expected_sha256: str | None = None) -> tuple[dict[str, Any], bytes, str]:
    directory, path, review_path = _candidate_paths(candidate_id)
    if directory.is_symlink() or not review_path.is_file() or review_path.is_symlink():
        _fail("RESOURCE_CANDIDATE_INVALID")
    candidate, encoded = _read_json(path, "RESOURCE_CANDIDATE_INVALID")
    actual_sha256 = hashlib.sha256(encoded).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        _fail("RESOURCE_CANDIDATE_SHA_MISMATCH")
    if not _candidate_is_valid(candidate, candidate_id, encoded):
        _fail("RESOURCE_CANDIDATE_INVALID")
    return candidate, encoded, actual_sha256


def _validated_producer(candidate: dict[str, Any], local_config: dict[str, Any]) -> dict[str, Any]:
    if _runtime_summary(local_config) != candidate["runtime"]:
        _fail("RESOURCE_RUNTIME_BINDING_MISMATCH")
    try:
        documents = read_producer_bundle(Path(local_config["private_runtime_root"]), candidate["producer"]["run_id"])
    except (OSError, ValueError):
        _fail("RESOURCE_PRODUCER_INVALID")
    bundle, output = documents["bundle"], documents["output"]
    if (
        output is None
        or _producer_summary(bundle) != candidate["producer"]
        or bundle["runtime_binding_sha256"]
        != candidate["runtime"]["producer_runtime_binding_sha256"]
    ):
        _fail("RESOURCE_PRODUCER_INVALID")
    proposals, omitted, blockers = _project_output(output)
    if (
        output["source_binding"]["source_sha256"] != candidate["source"]["source_sha256"]
        or len(output["source_binding"]["page_numbers"]) != candidate["source"]["page_count"]
        or proposals != candidate["publishable_proposals"]
        or omitted != candidate["omitted_items"]
        or blockers != candidate["critical_blockers"]
    ):
        _fail("RESOURCE_PRODUCER_INVALID")
    return output


def analyze(arguments: argparse.Namespace, environment: Mapping[str, str]) -> dict[str, Any]:
    metadata, _ = _read_json(Path(arguments.metadata), "RESOURCE_METADATA_INVALID")
    metadata = _validate_metadata(metadata)
    source_sha256, page_count = _inspect_pdf(Path(arguments.pdf))
    if page_count > arguments.page_ceiling:
        _fail("RESOURCE_PAGE_CEILING_EXCEEDED")
    library, _, library_sha256 = _read_library(Path(arguments.library_file))
    local_config = read_local_ai_config_from_environment(environment)
    runtime = _runtime_summary(local_config)
    source = {"source_sha256": source_sha256, "page_count": page_count, "metadata": metadata}
    base = {"library_revision": library["library_revision"], "library_sha256": library_sha256}
    ceilings = {"page_ceiling": arguments.page_ceiling, "latency_ceiling_seconds": arguments.latency_ceiling_seconds}
    telemetry_binding = {
        "external_network_calls": 0,
        "monetary_cost": 0,
        "peak_vram_bytes": "unavailable",
    }
    candidate_id = _candidate_id(
        _candidate_identity(source, base, runtime, ceilings, telemetry_binding)
    )
    directory, _, _ = _candidate_paths(candidate_id)
    if directory.exists():
        candidate, _, candidate_sha256 = _load_candidate(candidate_id)
        _validated_producer(candidate, local_config)
        candidate_path, review_path = _safe_paths(candidate_id)
        return {"status": "replay", "candidate_id": candidate_id, "candidate_sha256": candidate_sha256, "candidate_path": candidate_path, "review_path": review_path, "ocr_calls": 0, "qwen_attempts": 0}
    bundle = run_full_text_first_pdf(
        {"media_type": "application/pdf", "source_path": str(arguments.pdf), "expected_source_sha256": source_sha256},
        local_config,
    )
    try:
        documents = read_producer_bundle(Path(local_config["private_runtime_root"]), bundle["run_id"])
    except (OSError, ValueError):
        _fail("RESOURCE_PRODUCER_INVALID")
    bundle, output = documents["bundle"], documents["output"]
    if (
        output is None
        or bundle["processing"] == "failed"
        or bundle["page_count"] != page_count
        or output["source_binding"]["source_sha256"] != source_sha256
        or len(output["source_binding"]["page_numbers"]) != page_count
        or bundle["runtime_binding_sha256"] != runtime["producer_runtime_binding_sha256"]
        or bundle["ocr_calls"] > page_count
        or bundle["concept_calls"] > 2 * page_count
        or bundle["ocr_loads"] > 1
        or bundle["concept_loads"] > 1
        or bundle["duration_ms"] > arguments.latency_ceiling_seconds * 1000
    ):
        _fail("RESOURCE_PRODUCER_FAILED")
    proposals, omitted, blockers = _project_output(output)
    if not proposals or blockers:
        _fail("RESOURCE_CANDIDATE_NOT_PUBLISHABLE")
    candidate = {
        "schema": CANDIDATE_SCHEMA, "candidate_policy": CANDIDATE_POLICY,
        "candidate_id": candidate_id, "source": source, "base": base, "runtime": runtime,
        "ceilings": ceilings, "producer": _producer_summary(bundle),
        "publishable_proposals": proposals, "omitted_items": omitted,
        "critical_blockers": blockers, "processing": "partial", "quality": "needs_review",
        "decision": "review", "reason_codes": [REVIEW_REASON],
        "telemetry": {
            **telemetry_binding,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }
    candidate["candidate_content_sha256"] = _candidate_content_sha256(candidate)
    encoded = _candidate_bytes(candidate)
    candidate_sha256 = hashlib.sha256(encoded).hexdigest()
    _write_candidate(directory, encoded, _review_text(candidate, candidate_sha256))
    candidate_path, review_path = _safe_paths(candidate_id)
    return {"status": "analyzed", "candidate_id": candidate_id, "candidate_sha256": candidate_sha256, "candidate_path": candidate_path, "review_path": review_path, "ocr_calls": bundle["ocr_calls"], "qwen_attempts": bundle["concept_calls"]}


def _reviewed_inputs(library: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = [{key: deepcopy(value) for key, value in source.items() if key != "resource_id"} for source in library["sources"]]
    evidence_by_id = {item["evidence_id"]: item for item in library["evidence"]}
    source_by_resource = {item["resource_id"]: item["source_sha256"] for item in library["sources"]}
    entries = []
    for concept in library["concepts"]:
        if len(concept["evidence_ids"]) != 1:
            _fail("RESOURCE_LIBRARY_ROUND_TRIP_FAILED")
        evidence = evidence_by_id[concept["evidence_ids"][0]]
        entries.append({
            "source_sha256": source_by_resource[evidence["resource_id"]],
            "page_number": evidence["page_number"], "label": concept["label"],
            "quote": evidence["quote"], "region": deepcopy(evidence["region"]),
        })
    return sources, entries


def _candidate_formal_library(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate["source"]["metadata"]
    source = {key: deepcopy(value) for key, value in metadata.items() if key != "schema"}
    source.update({"source_sha256": candidate["source"]["source_sha256"], "page_count": candidate["source"]["page_count"]})
    entries = [{
        "source_sha256": candidate["source"]["source_sha256"],
        "page_number": proposal["page_number"], "label": proposal["label"],
        "quote": proposal["quote"], "region": deepcopy(proposal["region"]),
    } for proposal in candidate["publishable_proposals"]]
    return build_resource_library([source], entries)


def _presence(library: dict[str, Any], candidate_objects: dict[str, Any]) -> str:
    resource_id = candidate_objects["sources"][0]["resource_id"]
    current_sources = [item for item in library["sources"] if item["resource_id"] == resource_id]
    current_evidence = [item for item in library["evidence"] if item["resource_id"] == resource_id]
    page_refs = {item["page_ref"] for item in candidate_objects["concepts"]}
    current_concepts = [item for item in library["concepts"] if item["page_ref"] in page_refs]
    observed = bool(current_sources or current_evidence or current_concepts)
    if not observed:
        return "none"
    if (
        current_sources == candidate_objects["sources"]
        and current_evidence == candidate_objects["evidence"]
        and current_concepts == candidate_objects["concepts"]
    ):
        return "full"
    return "partial"


def _atomic_replace_library(path: Path, document: dict[str, Any]) -> int:
    encoded = canonical_bytes(document)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as destination:
            temporary_path = Path(destination.name)
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        checked, checked_bytes, _ = _read_library(temporary_path)
        if checked != document or checked_bytes != encoded:
            _fail("RESOURCE_LIBRARY_WRITE_FAILED")
        os.replace(temporary_path, path)
        temporary_path = None
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except ResourceIntakeError:
        raise
    except OSError:
        _fail("RESOURCE_LIBRARY_WRITE_FAILED")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return len(encoded)


def publish(arguments: argparse.Namespace, environment: Mapping[str, str]) -> dict[str, Any]:
    if arguments.confirm != arguments.candidate_id:
        _fail("RESOURCE_CONFIRMATION_MISMATCH")
    candidate, _, candidate_sha256 = _load_candidate(arguments.candidate_id, arguments.candidate_sha256)
    local_config = read_local_ai_config_from_environment(environment)
    _validated_producer(candidate, local_config)
    source_sha256, page_count = _inspect_pdf(Path(arguments.source_pdf))
    if (source_sha256, page_count) != (candidate["source"]["source_sha256"], candidate["source"]["page_count"]):
        _fail("RESOURCE_SOURCE_BINDING_MISMATCH")
    target = Path(arguments.library_file)
    library, encoded, library_sha256 = _read_library(target)
    candidate_objects = _candidate_formal_library(candidate)
    presence = _presence(library, candidate_objects)
    candidate_path, review_path = _safe_paths(arguments.candidate_id)
    if presence == "full":
        return {"status": "already_published", "candidate_id": arguments.candidate_id, "candidate_sha256": candidate_sha256, "old_revision": library["library_revision"], "new_revision": library["library_revision"], "source_count": len(library["sources"]), "evidence_count": len(library["evidence"]), "concept_count": len(library["concepts"]), "candidate_path": candidate_path, "review_path": review_path, "written_bytes": 0}
    if presence == "partial":
        _fail("RESOURCE_LIBRARY_CONFLICT")
    if (library["library_revision"], library_sha256) != (candidate["base"]["library_revision"], candidate["base"]["library_sha256"]):
        _fail("RESOURCE_BASE_LIBRARY_STALE")
    sources, entries = _reviewed_inputs(library)
    if canonical_bytes(build_resource_library(sources, entries)) != encoded:
        _fail("RESOURCE_LIBRARY_ROUND_TRIP_FAILED")
    new_source, new_entries = _reviewed_inputs(candidate_objects)
    new_library = build_resource_library(sources + new_source, entries + new_entries)
    written_bytes = _atomic_replace_library(target, new_library)
    return {"status": "published", "candidate_id": arguments.candidate_id, "candidate_sha256": candidate_sha256, "old_revision": library["library_revision"], "new_revision": new_library["library_revision"], "source_count": len(new_library["sources"]), "evidence_count": len(new_library["evidence"]), "concept_count": len(new_library["concepts"]), "candidate_path": candidate_path, "review_path": review_path, "written_bytes": written_bytes}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m learning_resources.resource_intake")
    commands = parser.add_subparsers(dest="command", required=True)
    analyze_parser = commands.add_parser("analyze")
    analyze_parser.add_argument("pdf")
    analyze_parser.add_argument("--metadata", required=True)
    analyze_parser.add_argument("--page-ceiling", required=True, type=int)
    analyze_parser.add_argument("--latency-ceiling-seconds", required=True, type=int)
    analyze_parser.add_argument("--library-file", default=str(DEFAULT_LIBRARY_FILE))
    publish_parser = commands.add_parser("publish")
    publish_parser.add_argument("candidate_id")
    publish_parser.add_argument("--candidate-sha256", required=True)
    publish_parser.add_argument("--confirm", required=True)
    publish_parser.add_argument("--source-pdf", required=True)
    publish_parser.add_argument("--library-file", default=str(DEFAULT_LIBRARY_FILE))
    return parser


def main(argv: list[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "analyze":
            if arguments.page_ceiling < 1 or arguments.latency_ceiling_seconds < 1:
                _fail("RESOURCE_CEILING_INVALID")
            result = analyze(arguments, os.environ if environment is None else environment)
        else:
            if re.fullmatch(r"[0-9a-f]{64}", arguments.candidate_sha256) is None:
                _fail("RESOURCE_CANDIDATE_SHA_MISMATCH")
            result = publish(arguments, os.environ if environment is None else environment)
        print(json.dumps(result, sort_keys=True))
        return 0
    except ResourceIntakeError as error:
        print(json.dumps({"status": "failed", "reason_code": str(error)}, sort_keys=True))
        return 1
    except Exception:
        print(json.dumps({"status": "failed", "reason_code": "RESOURCE_INTAKE_FAILED"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

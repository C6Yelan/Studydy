from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .artifact_reason_codes import formal_reason_codes, reason_codes_are_valid
from .concept_evidence_output import (
    AGGREGATION_POLICY,
    MAX_ARTIFACT_FILE_BYTES,
    validate_output_document,
)
from .ocr_page_evidence import canonical_bytes, canonical_sha256


BUNDLE_SCHEMA = "text-first-producer-bundle/v2"
_BUNDLE_FIELDS = {
    "schema", "aggregation_policy", "run_id", "produced_at",
    "runtime_binding_sha256", "page_count", "included_page_count",
    "excluded_page_count", "output_file", "output_id", "output_sha256",
    "output_size_bytes", "processing", "quality", "decision", "reason_codes",
    "duration_ms", "ocr_calls", "concept_calls", "ocr_loads", "concept_loads",
    "bundle_id",
}


def build_producer_bundle(
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
    output_bytes = canonical_bytes(output) if output is not None else None
    excluded_count = (
        len(output.get("excluded_pages", []))
        if output is not None
        else len(excluded_pages or [])
    )
    included_count = len(output.get("pages", [])) if output is not None else 0
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "aggregation_policy": AGGREGATION_POLICY,
        "run_id": run_id,
        "produced_at": produced_at,
        "runtime_binding_sha256": runtime_binding_sha256,
        "page_count": page_count or included_count + excluded_count,
        "included_page_count": included_count,
        "excluded_page_count": excluded_count,
        "output_file": "concept-evidence-output.json" if output is not None else None,
        "output_id": output["output_id"] if output is not None else None,
        "output_sha256": canonical_sha256(output) if output is not None else None,
        "output_size_bytes": len(output_bytes) if output_bytes is not None else 0,
        "processing": "failed" if output is None else output["processing"],
        "quality": "needs_review",
        "decision": "reject" if output is None else "review",
        "reason_codes": formal_reason_codes(reasons),
        "duration_ms": duration_ms,
        "ocr_calls": ocr_calls,
        "concept_calls": concept_calls,
        "ocr_loads": ocr_loads,
        "concept_loads": concept_loads,
    }
    bundle["bundle_id"] = f"text-first-producer-bundle:sha256:{canonical_sha256(bundle)}"
    return bundle


def validate_bundle_documents(
    bundle: Any,
    output: dict[str, Any] | None,
    run_id: str,
) -> bool:
    """重驗 completion metadata 與 optional output 的唯一 bundle 邊界。"""

    try:
        if not isinstance(bundle, dict) or set(bundle) != _BUNDLE_FIELDS:
            return False
        page_count = bundle["page_count"]
        included = bundle["included_page_count"]
        excluded = bundle["excluded_page_count"]
        if (
            bundle["schema"] != BUNDLE_SCHEMA
            or bundle["aggregation_policy"] != AGGREGATION_POLICY
            or bundle["run_id"] != run_id
            or re.fullmatch(r"text-first-run:[0-9a-fA-F-]{36}", run_id) is None
            or not isinstance(bundle["produced_at"], str)
            or not bundle["produced_at"]
            or re.fullmatch(r"[0-9a-f]{64}", bundle["runtime_binding_sha256"]) is None
            or type(page_count) is not int
            or page_count < 0
            or type(included) is not int
            or included < 0
            or type(excluded) is not int
            or excluded < 0
            or type(bundle["output_size_bytes"]) is not int
            or bundle["output_size_bytes"] < 0
            or type(bundle["duration_ms"]) is not int
            or bundle["duration_ms"] < 0
            or type(bundle["ocr_calls"]) is not int
            or not 0 <= bundle["ocr_calls"] <= page_count
            or type(bundle["concept_calls"]) is not int
            or not 0 <= bundle["concept_calls"] <= 2 * page_count
            or type(bundle["ocr_loads"]) is not int
            or bundle["ocr_loads"] not in {0, 1}
            or type(bundle["concept_loads"]) is not int
            or bundle["concept_loads"] not in {0, 1}
            or bundle["quality"] != "needs_review"
            or not reason_codes_are_valid(bundle["reason_codes"], formal=True)
            or bundle["reason_codes"] != sorted(set(bundle["reason_codes"]))
        ):
            return False
        identity = dict(bundle)
        bundle_id = identity.pop("bundle_id")
        if (
            bundle_id
            != f"text-first-producer-bundle:sha256:{canonical_sha256(identity)}"
            or len(canonical_bytes(bundle)) > MAX_ARTIFACT_FILE_BYTES
        ):
            return False
        if output is None:
            return (
                bundle["processing"] == "failed"
                and bundle["decision"] == "reject"
                and bundle["output_file"] is None
                and bundle["output_id"] is None
                and bundle["output_sha256"] is None
                and bundle["output_size_bytes"] == 0
                and included == 0
                and excluded <= page_count
            )
        output_bytes = canonical_bytes(output)
        return (
            validate_output_document(output)
            and page_count >= 1
            and output["run_id"] == run_id
            and bundle["output_file"] == "concept-evidence-output.json"
            and bundle["output_id"] == output["output_id"]
            and bundle["output_sha256"] == canonical_sha256(output)
            and bundle["output_size_bytes"] == len(output_bytes)
            and bundle["processing"] == output["processing"]
            and bundle["decision"] == "review"
            and included + excluded == page_count
            and included == len(output["pages"])
            and excluded == len(output["excluded_pages"])
        )
    except (KeyError, RecursionError, TypeError, ValueError):
        return False


def _write_new(path: Path, encoded: bytes) -> None:
    with path.open("xb") as destination:
        destination.write(encoded)
        destination.flush()
        os.fsync(destination.fileno())


def publish_run(
    runtime_root: Path,
    bundle: dict[str, Any],
    output: dict[str, Any] | None,
) -> Path:
    """完成檔案全數寫妥後，才以一次 rename 發布不可覆寫的 bundle。"""

    run_id = bundle.get("run_id") if isinstance(bundle, dict) else None
    if (
        runtime_root.is_symlink()
        or not isinstance(run_id, str)
        or not validate_bundle_documents(bundle, output, run_id)
    ):
        raise OSError("PRODUCER_BUNDLE_WRITE_FAILED")
    runs = runtime_root / "runs"
    if runs.is_symlink() or (runs.exists() and not runs.is_dir()):
        raise OSError("PRODUCER_BUNDLE_WRITE_FAILED")
    try:
        runs.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise OSError("PRODUCER_BUNDLE_WRITE_FAILED") from error
    destination = runs / run_id
    if os.path.lexists(destination):
        raise FileExistsError("ARTIFACT_COLLISION")
    try:
        stage = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
    except OSError as error:
        raise OSError("PRODUCER_BUNDLE_WRITE_FAILED") from error
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
        encoded_bundle = canonical_bytes(bundle)
        bundle_path = stage / "producer-bundle.json"
        try:
            _write_new(bundle_path, encoded_bundle)
            if bundle_path.read_bytes() != encoded_bundle:
                raise OSError
            directory_descriptor = os.open(runs, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
                os.replace(stage, destination)
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as error:
            raise OSError("PRODUCER_BUNDLE_WRITE_FAILED") from error
        return destination
    finally:
        if stage.exists():
            for child in stage.iterdir():
                child.unlink(missing_ok=True)
            stage.rmdir()


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("PRODUCER_BUNDLE_INVALID")
        value[key] = item
    return value


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    if isinstance(value, dict):
        for item in value.values():
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def _read_json_file(path: Path) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_ARTIFACT_FILE_BYTES
    ):
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
    _check_depth(value)
    return value


def read_producer_bundle(runtime_root: Path, run_id: str) -> dict[str, Any]:
    """只讀固定檔名，並重驗 bundle、optional output 與所有封閉 shape。"""

    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    runs = runtime_root / "runs"
    directory = runs / run_id
    if runtime_root.is_symlink() or runs.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    bundle = _read_json_file(directory / "producer-bundle.json")
    expected_files = {"producer-bundle.json"}
    output: dict[str, Any] | None = None
    if bundle.get("output_file") is not None:
        if bundle.get("output_file") != "concept-evidence-output.json":
            raise ValueError("PRODUCER_BUNDLE_INVALID")
        output = _read_json_file(directory / "concept-evidence-output.json")
        expected_files.add("concept-evidence-output.json")
    try:
        if (
            not validate_bundle_documents(bundle, output, run_id)
            or {item.name for item in directory.iterdir()} != expected_files
        ):
            raise ValueError("PRODUCER_BUNDLE_INVALID")
    except (KeyError, OSError, TypeError):
        raise ValueError("PRODUCER_BUNDLE_INVALID") from None
    return {"bundle": bundle, "output": output}


def remove_producer_bundle(runtime_root: Path, run_id: str) -> None:
    """只移除已驗證 run 的兩個固定 handoff 檔案。"""

    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise OSError("PRODUCER_BUNDLE_CLEANUP_FAILED")
    directory = runtime_root / "runs" / run_id
    if directory.is_symlink() or not directory.is_dir():
        raise OSError("PRODUCER_BUNDLE_CLEANUP_FAILED")
    try:
        for name in ("concept-evidence-output.json", "producer-bundle.json"):
            path = directory / name
            if path.is_symlink():
                raise OSError
            path.unlink(missing_ok=True)
        directory.rmdir()
    except OSError as error:
        raise OSError("PRODUCER_BUNDLE_CLEANUP_FAILED") from error

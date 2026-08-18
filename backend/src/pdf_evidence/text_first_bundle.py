"""原子發布並重驗文字優先 producer bundle。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .concept_evidence_output import (
    AGGREGATION_POLICY,
    BUNDLE_SCHEMA,
    MAX_BUNDLE_FILE_BYTES,
    OUTPUT_SCHEMA,
    TERMINAL_SCHEMA,
    validate_output_document,
)
from .ocr_page_evidence import canonical_bytes, canonical_sha256


_TERMINAL_FIELDS = {
    "schema", "aggregation_policy", "run_id", "produced_at", "output_id",
    "runtime_binding_sha256", "page_count", "included_page_count",
    "excluded_page_count", "processing", "quality", "decision", "reason_codes",
    "duration_ms", "ocr_calls", "concept_calls", "ocr_loads", "concept_loads",
}
_BUNDLE_FIELDS = {
    "schema", "run_id", "output_file", "output_sha256", "terminal_file",
    "terminal_sha256", "output_size_bytes", "terminal_size_bytes", "processing",
    "quality", "decision", "bundle_id",
}


def validate_terminal(terminal: Any, output: dict[str, Any] | None) -> bool:
    if not isinstance(terminal, dict) or set(terminal) != _TERMINAL_FIELDS:
        return False
    page_count = terminal["page_count"]
    included = terminal["included_page_count"]
    excluded = terminal["excluded_page_count"]
    if (
        terminal["schema"] != TERMINAL_SCHEMA
        or terminal["aggregation_policy"] != AGGREGATION_POLICY
        or type(page_count) is not int
        or not 0 <= page_count <= 100_000
        or type(included) is not int
        or type(excluded) is not int
        or included < 0
        or excluded < 0
        or type(terminal["duration_ms"]) is not int
        or terminal["duration_ms"] < 0
        or type(terminal["ocr_calls"]) is not int
        or not 0 <= terminal["ocr_calls"] <= page_count
        or type(terminal["concept_calls"]) is not int
        or not 0 <= terminal["concept_calls"] <= 2 * page_count
        or type(terminal["ocr_loads"]) is not int
        or terminal["ocr_loads"] not in {0, 1}
        or type(terminal["concept_loads"]) is not int
        or not 0 <= terminal["concept_loads"] <= page_count + 1
        or terminal["quality"] != "needs_review"
        or not isinstance(terminal["run_id"], str)
        or re.fullmatch(r"text-first-run:[0-9a-fA-F-]{36}", terminal["run_id"]) is None
        or not isinstance(terminal["runtime_binding_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", terminal["runtime_binding_sha256"]) is None
        or not isinstance(terminal["reason_codes"], list)
        or not terminal["reason_codes"]
        or not all(isinstance(reason, str) and reason for reason in terminal["reason_codes"])
        or terminal["reason_codes"] != sorted(set(terminal["reason_codes"]))
    ):
        return False
    if output is None:
        return (
            terminal["processing"] == "failed"
            and terminal["decision"] == "reject"
            and terminal["output_id"] is None
            and included == 0
            and excluded <= page_count
        )
    return (
        validate_output_document(output)
        and 1 <= page_count <= 32
        and terminal["run_id"] == output["run_id"]
        and terminal["output_id"] == output["output_id"]
        and terminal["processing"] == output["processing"]
        and terminal["decision"] == "review"
        and included + excluded == page_count
        and included == len(output["pages"])
        and excluded == len(output["excluded_pages"])
    )


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
        "quality": terminal["quality"],
        "decision": terminal["decision"],
    }
    bundle["bundle_id"] = f"text-first-producer-bundle:sha256:{canonical_sha256(bundle)}"
    return bundle


def validate_bundle_documents(
    bundle: Any,
    terminal: Any,
    output: dict[str, Any] | None,
    run_id: str,
) -> bool:
    """供 DB cutover 在寫入前重驗同一組 in-memory documents。"""

    try:
        return (
            isinstance(bundle, dict)
            and set(bundle) == _BUNDLE_FIELDS
            and bundle == _bundle_document(run_id, output, terminal)
            and validate_terminal(terminal, output)
            and terminal["run_id"] == run_id
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
        or not validate_terminal(terminal, output)
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
            if terminal_path.read_bytes() != encoded_terminal or bundle_path.read_bytes() != encoded_bundle:
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
    _check_depth(value)
    return value


def read_producer_bundle(runtime_root: Path, run_id: str) -> dict[str, Any]:
    """只讀固定檔名，並重驗 bundle、terminal、output 與所有封閉 shape。"""

    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise ValueError("PRODUCER_BUNDLE_INVALID")
    runs = runtime_root / "runs"
    directory = runs / run_id
    if runtime_root.is_symlink() or runs.is_symlink() or directory.is_symlink() or not directory.is_dir():
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
            not validate_bundle_documents(bundle, terminal, output, run_id)
            or {item.name for item in directory.iterdir()} != expected_files
            or (output is not None and output["schema"] != OUTPUT_SCHEMA)
        ):
            raise ValueError("PRODUCER_BUNDLE_INVALID")
    except (KeyError, OSError, TypeError):
        raise ValueError("PRODUCER_BUNDLE_INVALID") from None
    return {"bundle": bundle, "terminal": terminal, "output": output}

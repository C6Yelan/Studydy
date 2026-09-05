"""執行並評分私人 A40 final qualification，不輸出教材文字。"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from threading import Event, Thread
import time
from typing import Any

import pymupdf

from knowledge_map.structure import validate_knowledge_structure
from pdf_evidence.material_pipeline import analyze_material
from pdf_evidence.ocr_page_evidence import canonical_bytes, canonical_sha256
from runtime.local_app import read_local_ai_config_from_environment
from runtime.material_processing import runtime_preflight
from runtime.semantic_service import preflight_semantic_service


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".studydy-runtime/a40-final"
QUALITY_ROLES = ("array_45",)
RUN_ROLES = QUALITY_ROLES
ARRAY_SOURCE_SHA256 = "773e72dcddc7902d27d2315910749361928f143ac11beb6b66c5fe6b0d5b59df"
MATERIAL_REVIEW_FIELDS = {"revision", "reviewed_units", "usable_units", "limitations"}
ASSESSMENT_FIELDS = {"reviewed_questions", "usable_questions", "no_safe_requests", "false_mastery", "limitations"}
CLOSED_LOOP_FIELDS = {
    "upload", "progress", "ingestion", "evidence", "concepts", "relations", "map",
    "path", "study_session", "assessment", "answer", "learner_guidance", "reload_reopen",
    "exact_revision", "pdf_locator",
}
RUNTIME_FIELDS = {
    "oom", "engine_death", "python_minors", "mdeberta_decision",
}


class QualificationError(RuntimeError):
    pass


def _private_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / ".studydy-runtime").resolve()
    temporary = resolved.parent == Path("/tmp") and resolved.name.startswith("studydy-")
    if not temporary and resolved != allowed and allowed not in resolved.parents:
        raise QualificationError("QUALIFICATION_OUTPUT_NOT_PRIVATE")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    details = resolved.stat()
    if stat.S_IMODE(details.st_mode) != 0o700 or details.st_uid != os.geteuid():
        raise QualificationError("QUALIFICATION_OUTPUT_NOT_PRIVATE")
    return resolved


def _gpu() -> dict[str, Any]:
    executable = next((name for name in ("nvidia-smi", "nvidia-smi.exe") if shutil.which(name)), None)
    if executable is None:
        raise QualificationError("A40_UNAVAILABLE")
    completed = subprocess.run(
        [executable, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    rows = [line.split(",") for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(rows) != 1:
        raise QualificationError("A40_UNAVAILABLE")
    name, memory, driver = (part.strip() for part in rows[0])
    if "A40" not in name or int(memory) < 45_000:
        raise QualificationError("A40_IDENTITY_MISMATCH")
    return {"name": "NVIDIA A40", "memory_mib": int(memory), "driver": driver}


def _used_gpu_memory() -> int:
    executable = next((name for name in ("nvidia-smi", "nvidia-smi.exe") if shutil.which(name)), None)
    if executable is None:
        raise QualificationError("A40_UNAVAILABLE")
    completed = subprocess.run(
        [executable, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(rows) != 1:
        raise QualificationError("A40_TELEMETRY_UNAVAILABLE")
    try:
        return int(rows[0])
    except ValueError:
        raise QualificationError("A40_TELEMETRY_UNAVAILABLE") from None


class GpuMonitor:
    def __init__(self) -> None:
        self._stop = Event()
        self._thread = Thread(target=self._run, name="studydy-a40-monitor")
        self.peak_mib = 0
        self.error: Exception | None = None

    def start(self) -> None:
        self.peak_mib = _used_gpu_memory()
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.wait(0.5):
                self.peak_mib = max(self.peak_mib, _used_gpu_memory())
        except Exception as error:
            self.error = error

    def stop(self) -> int:
        self._stop.set()
        self._thread.join()
        self.peak_mib = max(self.peak_mib, _used_gpu_memory())
        if self.error is not None:
            raise QualificationError("A40_TELEMETRY_UNAVAILABLE") from self.error
        return self.peak_mib


def _resident_processes() -> list[dict[str, Any]]:
    processes = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
            lowered = command.casefold()
            engine = (entry / "comm").read_text().startswith("VLLM::Engine")
            if not engine and ("vllm" not in lowered or not any(
                marker in lowered for marker in (" serve ", "api_server")
            )):
                continue
            start_ticks = (entry / "stat").read_text().split()[21]
            processes.append({"pid": int(entry.name), "start_ticks": start_ticks})
        except (OSError, IndexError, ValueError):
            continue
    return sorted(processes, key=lambda process: process["pid"])


def _pdf_request(path: Path) -> tuple[dict[str, Any], int]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise QualificationError("QUALIFICATION_INPUT_INVALID")
    encoded = path.read_bytes()
    import hashlib
    source_sha256 = hashlib.sha256(encoded).hexdigest()
    try:
        with pymupdf.open(path) as document:
            page_count = document.page_count
    except Exception:
        raise QualificationError("QUALIFICATION_INPUT_INVALID") from None
    return {
        "media_type": "application/pdf",
        "source_path": str(path),
        "expected_source_sha256": source_sha256,
    }, page_count


def _write(path: Path, value: dict[str, Any]) -> None:
    encoded = canonical_bytes(value)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _read(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise QualificationError("QUALIFICATION_JSON_INVALID")
            value[key] = item
        return value

    def reject_constant(_: str) -> None:
        raise QualificationError("QUALIFICATION_JSON_INVALID")

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except QualificationError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise QualificationError("QUALIFICATION_JSON_INVALID") from None
    if not isinstance(value, dict):
        raise QualificationError("QUALIFICATION_JSON_INVALID")
    return value


def run(inputs: dict[str, Path], output: Path) -> int:
    output = _private_output(output)
    if (output / "run-summary.private.json").exists():
        raise QualificationError("QUALIFICATION_OUTPUT_ALREADY_EXISTS")
    gpu = _gpu()
    settings = read_local_ai_config_from_environment(os.environ)
    binding = runtime_preflight(settings)
    before = _resident_processes()
    if not before:
        raise QualificationError("RESIDENT_QWEN_PROCESS_COUNT_INVALID")
    summaries = {}
    monitor = GpuMonitor()
    monitor.start()
    try:
        for role in RUN_ROLES:
            request, page_count = _pdf_request(inputs[role])
            if page_count != 45 or request["expected_source_sha256"] != ARRAY_SOURCE_SHA256:
                raise QualificationError("ARRAY_MATERIAL_IDENTITY_MISMATCH")
            started = time.monotonic()
            structure = analyze_material(request, settings)
            elapsed = time.monotonic() - started
            if (
                not validate_knowledge_structure(structure)
                or structure["status"]["processing"] == "failed"
                or not structure["concepts"]
            ):
                raise QualificationError("KNOWLEDGE_STRUCTURE_INVALID")
            _write(output / f"{role}.private.json", structure)
            summaries[role] = {
                "source_sha256": structure["source_sha256"],
                "page_count": structure["page_count"],
                "revision": structure["revision"],
                "processing": structure["status"]["processing"],
                "concepts": len(structure["concepts"]),
                "relations": len(structure["relations"]),
                "path_steps": len(structure["initial_learning_path"]),
                "semantic_calls": structure["metrics"]["semantic_calls"],
                "ocr_calls": structure["metrics"]["ocr_calls"],
                "evidence_seconds": round(structure["metrics"]["evidence_duration_ms"] / 1000, 3),
                "semantic_seconds": round(structure["metrics"]["semantic_duration_ms"] / 1000, 3),
                "elapsed_seconds": round(elapsed, 3),
            }
            preflight_semantic_service(settings["runtime_lock"])
    finally:
        peak_vram_mib = monitor.stop()
    after = _resident_processes()
    if before != after:
        raise QualificationError("RESIDENT_QWEN_RELOADED")
    summary = {
        "schema": "a40-final-run/v2",
        "produced_at": datetime.now(UTC).isoformat(),
        "candidate_sha": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip(),
        "runtime_binding_sha256": binding["runtime_binding_sha256"],
        "gpu": gpu,
        "peak_vram_mib": peak_vram_mib,
        "resident_qwen": {
            "server_processes": before,
            "served_model_load_count": 1,
            "loads_during_run": 0,
        },
        "materials": summaries,
    }
    summary["run_sha256"] = canonical_sha256(summary)
    _write(output / "run-summary.private.json", summary)
    print(json.dumps({"status": "review_required", "run_sha256": summary["run_sha256"]}, sort_keys=True))
    return 0


def _automatic_gates(structure: dict[str, Any]) -> dict[str, Any]:
    concept_ids = {concept["concept_id"] for concept in structure["concepts"]}
    path_ids = [step["concept_id"] for step in structure["initial_learning_path"]]
    return {
        "structure_valid": validate_knowledge_structure(structure),
        "path_complete": bool(concept_ids) and len(path_ids) == len(concept_ids) and set(path_ids) == concept_ids,
    }


def _review_counts(review: dict[str, Any], total_name: str, usable_name: str) -> bool:
    """語意使用人工檢視的明確分母；限制列出，不宣稱自動保證準確度。"""
    total, usable = review[total_name], review[usable_name]
    if (type(total) is not int or type(usable) is not int or total < 0 or not 0 <= usable <= total
        or not isinstance(review["limitations"], list)
        or any(not isinstance(item, str) for item in review["limitations"])):
        raise QualificationError("QUALIFICATION_REVIEW_INVALID")
    return total > 0 and usable / total >= 0.85


def score(review_path: Path, output: Path) -> int:
    output = _private_output(output)
    run_summary = _read(output / "run-summary.private.json")
    review = _read(review_path)
    if (
        set(review) != {"schema", "run_sha256", "materials", "assessment", "closed_loop", "runtime"}
        or review.get("schema") != "a40-final-review/v2"
        or review.get("run_sha256") != run_summary.get("run_sha256")
        or canonical_sha256({key: value for key, value in run_summary.items() if key != "run_sha256"}) != run_summary.get("run_sha256")
        or run_summary.get("candidate_sha") != subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
        or set(review["materials"]) != set(QUALITY_ROLES)
        or set(review["assessment"]) != ASSESSMENT_FIELDS
        or set(review["closed_loop"]) != CLOSED_LOOP_FIELDS
        or set(review["runtime"]) != RUNTIME_FIELDS
    ):
        raise QualificationError("QUALIFICATION_REVIEW_BINDING_INVALID")
    structure = _read(output / "array_45.private.json")
    human = review["materials"]["array_45"]
    if set(human) != MATERIAL_REVIEW_FIELDS:
        raise QualificationError("QUALIFICATION_REVIEW_INVALID")
    automatic = _automatic_gates(structure)
    material_pass = _review_counts(human, "reviewed_units", "usable_units") and all(automatic.values()) and (
        structure["revision"] == human["revision"] == run_summary["materials"]["array_45"]["revision"]
        and structure["source_sha256"] == run_summary["materials"]["array_45"]["source_sha256"] == ARRAY_SOURCE_SHA256
        and structure["page_count"] == 45
    )
    assessment = review["assessment"]
    assessment_pass = _review_counts(assessment, "reviewed_questions", "usable_questions")
    if any(type(assessment[name]) is not int or assessment[name] < 0 for name in ("no_safe_requests", "false_mastery")):
        raise QualificationError("QUALIFICATION_REVIEW_INVALID")
    assessment_pass = assessment_pass and assessment["false_mastery"] == 0
    if any(type(value) is not bool for value in review["closed_loop"].values()):
        raise QualificationError("QUALIFICATION_REVIEW_INVALID")
    closed_loop_pass = all(review["closed_loop"].values())
    runtime = review["runtime"]
    if any(type(runtime[name]) is not int or runtime[name] < 0 for name in ("oom", "engine_death")):
        raise QualificationError("QUALIFICATION_REVIEW_INVALID")
    runtime_pass = (
        run_summary["resident_qwen"]["loads_during_run"] == 0
        and run_summary["resident_qwen"]["served_model_load_count"] == 1
        and bool(run_summary["resident_qwen"]["server_processes"])
        and run_summary["peak_vram_mib"] <= run_summary["gpu"]["memory_mib"]
        and runtime["oom"] == 0 and runtime["engine_death"] == 0
        and runtime["python_minors"] == ["3.12"] and runtime["mdeberta_decision"] == "REMOVE"
    )
    result = {"schema": "a40-final-qualification/v2", "candidate_sha": run_summary["candidate_sha"],
              "run_sha256": run_summary["run_sha256"], "pass": material_pass and assessment_pass and closed_loop_pass and runtime_pass,
              "material_pass": material_pass, "assessment_pass": assessment_pass, "closed_loop_pass": closed_loop_pass,
              "runtime_pass": runtime_pass, "automatic": automatic, "mdeberta_decision": "REMOVE"}
    _write(output / "qualification-summary.json", result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "score"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--array", type=Path)
    parser.add_argument("--review", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "run":
            if arguments.array is None:
                raise QualificationError("QUALIFICATION_INPUT_MISSING")
            return run({"array_45": arguments.array}, arguments.output)
        if arguments.review is None:
            raise QualificationError("QUALIFICATION_REVIEW_MISSING")
        return score(arguments.review, arguments.output)
    except (QualificationError, KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "failed", "reason": str(error) if isinstance(error, QualificationError) else "QUALIFICATION_INVALID"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

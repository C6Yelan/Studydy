import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/a40_final_qualification.py"
SPEC = importlib.util.spec_from_file_location("a40_final_qualification", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


def test_review_example_exactly_matches_scoring_contract():
    example = json.loads(
        (SCRIPT.with_name("a40_final_review.example.json")).read_text()
    )
    assert set(example) == {
        "schema", "run_sha256", "materials", "assessment", "closed_loop", "runtime"
    }
    assert set(example["materials"]) == set(qualification.QUALITY_ROLES)
    assert all(
        set(review) == qualification.MATERIAL_REVIEW_FIELDS
        for review in example["materials"].values()
    )
    assert set(example["assessment"]) == qualification.ASSESSMENT_FIELDS
    assert set(example["closed_loop"]) == qualification.CLOSED_LOOP_FIELDS
    assert set(example["runtime"]) == qualification.RUNTIME_FIELDS


def test_qualification_outputs_cannot_leave_ignored_private_root(tmp_path):
    with pytest.raises(qualification.QualificationError, match="QUALIFICATION_OUTPUT_NOT_PRIVATE"):
        qualification._private_output(tmp_path)


def test_gpu_monitor_records_peak_without_material_content(monkeypatch):
    monkeypatch.setattr(qualification, "_used_gpu_memory", lambda: 1234)
    monitor = qualification.GpuMonitor()
    monitor.start()
    assert monitor.stop() == 1234


def test_lifecycle_includes_linux_truncated_engine_process_name(tmp_path, monkeypatch):
    """Linux comm 只保留短名稱，仍須偵測 engine 重啟，不能只比 API server。"""
    process = tmp_path / "628"
    process.mkdir()
    (process / "cmdline").write_bytes(b"VLLM::EngineCore\0")
    (process / "comm").write_text("VLLM::EngineCor\n")
    (process / "stat").write_text(" ".join(["0"] * 21 + ["12345"]))
    monkeypatch.setattr(qualification, "Path", lambda value: tmp_path if value == "/proc" else Path(value))
    assert qualification._resident_processes() == [{"pid": 628, "start_ticks": "12345"}]


@pytest.mark.parametrize("usable_units, oom, expected_exit", [(17, 0, 0), (16, 0, 1), (17, 1, 1)])
def test_score_uses_85_percent_semantics_and_keeps_runtime_failures_blocking(
    tmp_path, monkeypatch, usable_units, oom, expected_exit
):
    monkeypatch.setattr(qualification, "ROOT", tmp_path)
    monkeypatch.setattr(
        qualification.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="candidate\n"),
    )
    monkeypatch.setattr(qualification, "_automatic_gates", lambda structure: {
        "structure_valid": True, "path_complete": True,
    })
    output = qualification._private_output(tmp_path / ".studydy-runtime" / "fresh")
    review = json.loads(SCRIPT.with_name("a40_final_review.example.json").read_text())
    summary = {
        "candidate_sha": "candidate",
        "resident_qwen": {
            "loads_during_run": 0, "served_model_load_count": 1,
            "server_processes": [{"pid": 123}],
        },
        "materials": {},
        "peak_vram_mib": 40000, "gpu": {"memory_mib": 46080},
    }
    for role in qualification.QUALITY_ROLES:
        structure = {"revision": role, "source_sha256": qualification.ARRAY_SOURCE_SHA256, "page_count": 45, "concepts": [{}]}
        qualification._write(output / f"{role}.private.json", structure)
        summary["materials"][role] = {
            "revision": role, "source_sha256": qualification.ARRAY_SOURCE_SHA256,
        }
        review["materials"][role].update(
            revision=role, reviewed_units=20, usable_units=usable_units,
        )
    review["assessment"].update(reviewed_questions=20, usable_questions=17)
    review["runtime"]["oom"] = oom
    review["closed_loop"] = dict.fromkeys(qualification.CLOSED_LOOP_FIELDS, True)
    summary["run_sha256"] = qualification.canonical_sha256(summary)
    review["run_sha256"] = summary["run_sha256"]
    qualification._write(output / "run-summary.private.json", summary)
    review_path = output / "review.private.json"
    qualification._write(review_path, review)

    assert qualification.score(review_path, output) == expected_exit
    scored = qualification._read(output / "qualification-summary.json")
    assert scored["runtime_pass"] is (oom == 0)
    assert scored["pass"] is (expected_exit == 0)

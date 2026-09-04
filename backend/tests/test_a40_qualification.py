import importlib.util
import json
from pathlib import Path

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

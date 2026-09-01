from copy import deepcopy
import json
from pathlib import Path

import pytest

import learning_adaptation.assessment_runtime as runtime
from learning_adaptation.assessment_runtime import (
    AssessmentRuntimeError,
    _validate_assessment_runtime_lock,
    assessment_runtime_binding,
)


ROOT = Path(__file__).parents[2]
MATERIAL_LOCK = json.loads(
    (ROOT / "local_ai/runtime-lock.json").read_text(encoding="utf-8")
)
ASSESSMENT_LOCK = json.loads(
    (ROOT / "local_ai/assessment-runtime-lock.json").read_text(
        encoding="utf-8"
    )
)


def test_assessment_lock_is_exact_and_tamper_closed():
    _validate_assessment_runtime_lock(ASSESSMENT_LOCK, MATERIAL_LOCK)
    changed = deepcopy(ASSESSMENT_LOCK)
    changed["verifier"]["entailment_margin_threshold"] = 0.01
    with pytest.raises(
        AssessmentRuntimeError, match="^ASSESSMENT_CONFIGURATION_INVALID$"
    ):
        _validate_assessment_runtime_lock(changed, MATERIAL_LOCK)


def test_assessment_binding_owns_policy_without_material_prompt_identity(
    monkeypatch,
):
    hashes_by_name = {
        Path(relative_path).name: ASSESSMENT_LOCK["code_hashes"][name]
        for name, relative_path in runtime._CODE_PATHS.items()
    }
    monkeypatch.setattr(
        runtime,
        "_file_sha256",
        lambda path: hashes_by_name[path.name],
    )
    binding = assessment_runtime_binding(
        {
            "runtime_lock": MATERIAL_LOCK,
            "site_packages": "/fixed/site-packages",
        },
        ASSESSMENT_LOCK,
    )

    assert binding["schema"] == "assessment-generation-runtime-binding/v1"
    assert binding["policy_revision"] == "assessment-generation-policy/v5"
    assert binding["assessment_runtime_lock_sha256"] == (
        runtime.ASSESSMENT_RUNTIME_LOCK_SHA256
    )
    encoded = json.dumps(binding, sort_keys=True)
    assert MATERIAL_LOCK["semantic"]["prompt"] not in encoded
    assert "formal_resolution" not in encoded

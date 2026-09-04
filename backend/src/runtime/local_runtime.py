from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from learning_adaptation.assessment_runtime import (
    assessment_runtime_binding,
    load_assessment_runtime_lock,
)
from learning_adaptation.assessment_verifier import start_assessment_process
from pdf_evidence.local_ai_process import (
    LocalAIError,
    start_ocr_process,
)
from pdf_evidence.text_first_run import material_analysis_lock

from .local_app import read_local_ai_config_from_environment
from .material_processing import _runtime_error, formal_runtime_preflight


def _verify_model_loads(local_config: dict[str, Any]) -> None:
    assessment_lock = load_assessment_runtime_lock()
    assessment_runtime_binding(local_config, assessment_lock)
    try:
        ocr = start_ocr_process(local_config)
        ocr.close()
    except LocalAIError:
        raise _runtime_error("ocr_model", "LOCAL_RUNTIME_SMOKE_FAILED") from None
    try:
        verifier = start_assessment_process(
            local_config,
            assessment_lock["verifier"]["startup_timeout_seconds"],
        )
        verifier.close()
    except LocalAIError:
        raise _runtime_error(
            "verifier_model", "LOCAL_RUNTIME_SMOKE_FAILED"
        ) from None


def verify_local_runtime(local_config: dict[str, Any]) -> dict[str, Any]:
    """以現有production loaders驗證本機runtime具備必要能力。"""

    formal_runtime_preflight(local_config)
    with material_analysis_lock(Path(local_config["private_runtime_root"])):
        _verify_model_loads(local_config)
    return {
        "status": "succeeded",
        "command": "verify",
    }


def _failure(error: Exception) -> dict[str, Any]:
    component = getattr(error, "component", None)
    reason = getattr(error, "reason", None)
    return {
        "status": "failed",
        "command": "verify",
        "component": component if component is not None else "layout",
        "reason": (
            reason
            if reason is not None
            else "LOCAL_RUNTIME_SETTINGS_MISMATCH"
        ),
    }


def main(
    argv: list[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments != ["verify"]:
            raise _runtime_error("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
        local_config = read_local_ai_config_from_environment(
            os.environ if environment is None else environment
        )
        print(json.dumps(verify_local_runtime(local_config), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps(_failure(error), sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

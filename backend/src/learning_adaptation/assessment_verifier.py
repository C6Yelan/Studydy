from __future__ import annotations

from typing import Any

from pdf_evidence.local_ai_process import LocalAIError, LocalAIProcess


_ASSESSMENT_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from studydy_local_ai.assessment_process import main;raise SystemExit(main())"
)


def start_assessment_process(
    settings: dict[str, Any], startup_timeout_seconds: float
) -> LocalAIProcess:
    """啟動只服務Assessment、具有獨立protocol identity的NLI child。"""

    process = LocalAIProcess(
        [
            settings["python_executable"],
            "-I",
            "-c",
            _ASSESSMENT_BOOTSTRAP,
            settings["site_packages"],
            settings["verifier_model_root"],
        ],
        request_limit=128 * 1024,
        response_limit=4096,
    )
    try:
        startup = process.read_startup_response(startup_timeout_seconds)
    except LocalAIError as error:
        process.abort()
        if error.reason_code == "CHILD_TIMEOUT":
            raise LocalAIError("ASSESSMENT_VERIFIER_TIMEOUT") from None
        if error.reason_code == "CHILD_RESPONSE_INVALID":
            raise LocalAIError("ASSESSMENT_VERIFIER_RESPONSE_INVALID") from None
        raise LocalAIError("ASSESSMENT_VERIFIER_UNAVAILABLE") from None
    ready = {
        "schema": "local-assessment-verifier-startup/v1",
        "status": "ready",
    }
    failure_reasons = {
        "ASSESSMENT_VERIFIER_DEPENDENCY_MISSING",
        "ASSESSMENT_VERIFIER_CUDA_UNAVAILABLE",
        "ASSESSMENT_VERIFIER_MODEL_LOAD_FAILED",
    }
    if startup == ready:
        return process
    if (
        set(startup) == {"schema", "status", "reason_code"}
        and startup.get("schema") == ready["schema"]
        and startup.get("status") == "failed"
        and startup.get("reason_code") in failure_reasons
    ):
        process.abort()
        raise LocalAIError(startup["reason_code"])
    process.abort()
    raise LocalAIError("ASSESSMENT_VERIFIER_RESPONSE_INVALID")

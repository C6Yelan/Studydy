from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import hashlib
import json
from pathlib import Path
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256
from runtime.material_processing import (
    MaterialProcessingError,
    formal_runtime_preflight,
)


ASSESSMENT_RUNTIME_LOCK_SHA256 = (
    "c5228601b11b7f38e5079cb159632dee9365bcc487ca00a975b9d61e1a3a9242"
)
_CODE_PATHS = {
    "backend_assessment_generation": (
        "backend/src/learning_adaptation/assessment_generation.py"
    ),
    "backend_assessment_items": (
        "backend/src/learning_adaptation/assessment_items.py"
    ),
    "backend_map_context": "backend/src/learning_adaptation/map_context.py",
    "backend_assessment_model_api": (
        "backend/src/learning_adaptation/assessment_model_api.py"
    ),
    "backend_assessment_verifier": (
        "backend/src/learning_adaptation/assessment_verifier.py"
    ),
    "local_assessment_process": (
        "local_ai/src/studydy_local_ai/assessment_process.py"
    ),
}


class AssessmentRuntimeError(RuntimeError):
    """Assessment runtime binding無法安全建立。"""


def _error() -> AssessmentRuntimeError:
    return AssessmentRuntimeError("ASSESSMENT_CONFIGURATION_INVALID")


def _file_sha256(path: Path) -> str:
    try:
        with path.open("rb") as source:
            digest = sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        raise _error() from None


def load_assessment_runtime_lock() -> dict[str, Any]:
    path = (
        Path(__file__).resolve().parents[3]
        / "local_ai"
        / "assessment-runtime-lock.json"
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _error() from None
    if not isinstance(value, dict):
        raise _error()
    return value


def _validate_assessment_runtime_lock(
    assessment_lock: Any, material_lock: Any
) -> None:
    try:
        semantic = material_lock["semantic"]
        relation = material_lock["relation_verifier"]
        matches = (
            isinstance(assessment_lock, dict)
            and canonical_sha256(assessment_lock)
            == ASSESSMENT_RUNTIME_LOCK_SHA256
            and assessment_lock["schema"]
            == "studydy-assessment-runtime-lock/v1"
            and assessment_lock["policy_revision"]
            == "assessment-generation-policy/v2"
            and assessment_lock["shared_models"]
            == {
                "semantic_model_id": semantic["model_id"],
                "semantic_revision": semantic["revision"],
                "verifier_model_id": relation["model_id"],
                "verifier_revision": relation["revision"],
            }
            and all(
                hashlib.sha256(
                    assessment_lock[stage]["prompt"].encode("utf-8")
                ).hexdigest()
                == assessment_lock[stage]["prompt_sha256"]
                for stage in ("proposal", "repair")
            )
            and assessment_lock["proposal"]["generation"]
            == {"temperature": 0, "max_tokens": 2800}
            and assessment_lock["repair"]["generation"]
            == {"temperature": 0, "max_tokens": 3400}
            and assessment_lock["proposal"]["retry"]
            == assessment_lock["repair"]["retry"]
            == {
                "max_attempts": 2,
                "retryable_reasons": [
                    "CONCEPT_API_TIMEOUT",
                    "CONCEPT_API_UNAVAILABLE",
                ],
            }
            and assessment_lock["proposal"]["input_serialization"]
            == assessment_lock["repair"]["input_serialization"]
            == "insertion-order-compact-json/v1"
            and assessment_lock["verifier"]
            == {
                "request_schema": "local-assessment-verifier-request/v1",
                "response_schema": "local-assessment-verifier-response/v2",
                "startup_schema": "local-assessment-verifier-startup/v1",
                "startup_failure_reasons": [
                    "ASSESSMENT_VERIFIER_DEPENDENCY_MISSING",
                    "ASSESSMENT_VERIFIER_CUDA_UNAVAILABLE",
                    "ASSESSMENT_VERIFIER_MODEL_LOAD_FAILED",
                ],
                "decision_rule": (
                    "relative-entailment-margin-with-risk-repair/v1"
                ),
                "correct_grounding_scope": (
                    "selected-evidence-relative-margin/v1"
                ),
                "ambiguity_risk_scope": "full-claim-evidence/v1",
                "entailment_margin_threshold": 0.1,
                "multiple_support_risk_threshold": 0.4,
                "maximum_pair_tokens": 384,
                "over_limit_policy": "reject-before-inference/v1",
                "startup_timeout_seconds": 120,
                "request_timeout_seconds": 120,
                "safe_loading": "safetensors-local-only-no-remote-code",
            }
            and assessment_lock["selection"]
            == {
                "ranking": (
                    "margin-descending-candidate-index-ascending/v1"
                ),
                "used_identity_scope": (
                    "study-session-target-claim-question-id/v1"
                ),
                "all_safe_candidates_used": "reject-no-new-safe-item/v1",
                "repair_exhaustion": (
                    "continue-ranked-safe-proposals/v1"
                ),
            }
            and assessment_lock["limits"]
            == {
                "candidate_count": 3,
                "proposal_distractor_count": 3,
                "repair_distractor_proposal_count": 5,
                "final_option_count": 4,
                "maximum_evidence_characters": 32768,
            }
            and set(assessment_lock["code_hashes"]) == set(_CODE_PATHS)
            and set(assessment_lock["package_sources"])
            == {"assessment_process.py"}
            and assessment_lock["package_sources"]["assessment_process.py"]
            == assessment_lock["code_hashes"]["local_assessment_process"]
            and assessment_lock["failure_policy"]
            == "reject-without-unsafe-fallback/v1"
            and assessment_lock["rationale_policy"]
            == "deterministic-selected-exact-evidence/v1"
        )
    except (KeyError, RecursionError, TypeError, ValueError):
        matches = False
    if not matches:
        raise _error()


def assessment_runtime_binding(
    local_config: Any, assessment_lock: Any
) -> dict[str, Any]:
    """建立只含Agent 4 policy/code與共用實體模型的exact binding。"""

    try:
        material_lock = local_config["runtime_lock"]
    except (KeyError, TypeError):
        raise _error() from None
    _validate_assessment_runtime_lock(assessment_lock, material_lock)
    repository_root = Path(__file__).resolve().parents[3]
    code_hashes = {
        name: _file_sha256(repository_root / relative_path)
        for name, relative_path in _CODE_PATHS.items()
    }
    if code_hashes != assessment_lock["code_hashes"]:
        raise _error()
    try:
        installed_process = (
            Path(local_config["site_packages"])
            / "studydy_local_ai"
            / "assessment_process.py"
        )
    except (KeyError, TypeError):
        raise _error() from None
    if _file_sha256(installed_process) != assessment_lock[
        "package_sources"
    ]["assessment_process.py"]:
        raise _error()
    binding = {
        "schema": "assessment-generation-runtime-binding/v1",
        "assessment_runtime_lock_sha256": canonical_sha256(assessment_lock),
        "policy_revision": assessment_lock["policy_revision"],
        "code_hashes": code_hashes,
        "physical_runtime": {
            "python": deepcopy(material_lock["python"]),
            "torch": material_lock["packages"]["torch"],
            "transformers": material_lock["packages"]["transformers"],
            "semantic_model": {
                "model_id": material_lock["semantic"]["model_id"],
                "revision": material_lock["semantic"]["revision"],
                "binding_manifest_sha256": material_lock["semantic"][
                    "binding_manifest_sha256"
                ],
                "server": deepcopy(material_lock["semantic"]["server"]),
            },
            "verifier_model": {
                "model_id": material_lock["relation_verifier"]["model_id"],
                "revision": material_lock["relation_verifier"]["revision"],
                "required_files_sha256": canonical_sha256(
                    material_lock["relation_verifier"]["required_files"]
                ),
            },
        },
    }
    binding["runtime_binding_sha256"] = canonical_sha256(binding)
    return binding


def assessment_runtime_preflight(
    local_config: Any, assessment_lock: Any
) -> dict[str, Any]:
    """先驗證共用實體安裝，再建立獨立Assessment runtime identity。"""

    try:
        formal_runtime_preflight(local_config)
    except MaterialProcessingError:
        raise _error() from None
    return assessment_runtime_binding(local_config, assessment_lock)

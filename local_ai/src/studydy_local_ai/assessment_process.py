"""以固定離線mDeBERTa回傳Assessment四選項的entailment probabilities。"""

from __future__ import annotations

import gc
from pathlib import Path
import re
import sys
from typing import Any

from .protocol import (
    ProtocolError,
    read_ndjson,
    write_ndjson,
)


MAXIMUM_TOKENS = 384
ASSESSMENT_REQUEST_SCHEMA = "local-assessment-verifier-request/v1"
ASSESSMENT_RESPONSE_SCHEMA = "local-assessment-verifier-response/v2"
ASSESSMENT_STARTUP_SCHEMA = "local-assessment-verifier-startup/v1"
MAX_ASSESSMENT_REQUEST_BYTES = 128 * 1024
MAX_ASSESSMENT_RESPONSE_BYTES = 4096
DEPENDENCY_MISSING = "ASSESSMENT_VERIFIER_DEPENDENCY_MISSING"
CUDA_UNAVAILABLE = "ASSESSMENT_VERIFIER_CUDA_UNAVAILABLE"
MODEL_LOAD_FAILED = "ASSESSMENT_VERIFIER_MODEL_LOAD_FAILED"
INPUT_TOO_LARGE = "ASSESSMENT_VERIFIER_INPUT_TOO_LARGE"
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class AssessmentRuntimeError(RuntimeError):
    """只攜帶Assessment verifier startup固定reason code。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class AssessmentInputTooLarge(RuntimeError):
    """完整Evidence-option pair超出已qualification的token boundary。"""


def validate_assessment_request(request: Any) -> dict[str, Any]:
    if (
        not isinstance(request, dict)
        or set(request) != {"schema", "request_id", "premise", "options"}
        or request.get("schema") != ASSESSMENT_REQUEST_SCHEMA
        or not isinstance(request.get("request_id"), str)
        or _REQUEST_ID.fullmatch(request["request_id"]) is None
    ):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    premise = request.get("premise")
    options = request.get("options")
    if (
        not isinstance(premise, str)
        or not premise.strip()
        or len(premise) > 32_768
        or any(ord(character) < 32 and character not in "\n\t" for character in premise)
        or not isinstance(options, list)
        or len(options) != 4
        or any(
            not isinstance(option, str)
            or not option.strip()
            or len(option) > 4096
            or any(
                ord(character) < 32 and character not in "\n\t"
                for character in option
            )
            for option in options
        )
    ):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    return {
        "request_id": request["request_id"],
        "premise": premise,
        "options": options,
    }


def load_assessment_model(model_root: Path) -> tuple[Any, Any, int]:
    try:
        import torch
        from transformers import (
            AutoConfig,
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )
    except ImportError:
        raise AssessmentRuntimeError(DEPENDENCY_MISSING) from None
    try:
        has_cuda = torch.cuda.is_available()
    except RuntimeError:
        has_cuda = False
    if not has_cuda:
        raise AssessmentRuntimeError(CUDA_UNAVAILABLE)
    try:
        configuration = AutoConfig.from_pretrained(
            model_root, local_files_only=True, trust_remote_code=False
        )
        labels = {
            str(label).casefold(): int(index)
            for index, label in configuration.id2label.items()
        }
        if labels != {"entailment": 0, "neutral": 1, "contradiction": 2}:
            raise ValueError("MODEL_OUTPUT_INVALID")
        tokenizer = AutoTokenizer.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=False,
            use_fast=True,
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().cuda()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise AssessmentRuntimeError(MODEL_LOAD_FAILED) from None
    return model, tokenizer, labels["entailment"]


def score_options(
    model: Any,
    tokenizer: Any,
    entailment_index: int,
    premise: str,
    options: list[str],
) -> list[float]:
    import torch

    tokens = tokenizer(
        [premise] * 4,
        options,
        truncation=False,
        padding=True,
        return_tensors="pt",
    )
    try:
        pair_tokens = int(tokens["input_ids"].shape[-1])
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError("MODEL_OUTPUT_INVALID") from None
    if pair_tokens > MAXIMUM_TOKENS:
        raise AssessmentInputTooLarge
    tokens = tokens.to(next(model.parameters()).device)
    with torch.inference_mode():
        probabilities = torch.softmax(model(**tokens).logits, dim=-1).cpu()
    values = [float(row[entailment_index]) for row in probabilities]
    if len(values) != 4 or any(value < 0 or value > 1 for value in values):
        raise ValueError("MODEL_OUTPUT_INVALID")
    return values


def _write_startup(*, reason_code: str | None = None) -> None:
    response = {
        "schema": ASSESSMENT_STARTUP_SCHEMA,
        "status": "ready" if reason_code is None else "failed",
    }
    if reason_code is not None:
        response["reason_code"] = reason_code
    write_ndjson(sys.stdout.buffer, response, MAX_ASSESSMENT_RESPONSE_BYTES)


def serve(model: Any, tokenizer: Any, entailment_index: int) -> None:
    while True:
        request = read_ndjson(sys.stdin.buffer, MAX_ASSESSMENT_REQUEST_BYTES)
        if request is None:
            return
        checked = validate_assessment_request(request)
        try:
            probabilities = score_options(
                model,
                tokenizer,
                entailment_index,
                checked["premise"],
                checked["options"],
            )
            response = {
                "schema": ASSESSMENT_RESPONSE_SCHEMA,
                "request_id": checked["request_id"],
                "status": "scored",
                "entailment_probabilities": probabilities,
            }
        except AssessmentInputTooLarge:
            response = {
                "schema": ASSESSMENT_RESPONSE_SCHEMA,
                "request_id": checked["request_id"],
                "status": "rejected",
                "reason_code": INPUT_TOO_LARGE,
            }
        write_ndjson(
            sys.stdout.buffer, response, MAX_ASSESSMENT_RESPONSE_BYTES
        )


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    model = tokenizer = None
    try:
        model, tokenizer, entailment_index = load_assessment_model(Path(sys.argv[1]))
        _write_startup()
        serve(model, tokenizer, entailment_index)
        return 0
    except AssessmentRuntimeError as error:
        try:
            _write_startup(reason_code=error.reason_code)
        except (OSError, ProtocolError):
            pass
        return 1
    except (OSError, ProtocolError, RuntimeError, ValueError):
        return 1
    finally:
        del model
        del tokenizer
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except (ImportError, RuntimeError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())

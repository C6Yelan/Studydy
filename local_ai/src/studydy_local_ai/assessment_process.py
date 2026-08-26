"""以固定離線mDeBERTa回傳Assessment四選項的entailment probabilities。"""

from __future__ import annotations

import gc
from pathlib import Path
import sys
from typing import Any

from .protocol import (
    ASSESSMENT_RESPONSE_SCHEMA,
    ASSESSMENT_STARTUP_SCHEMA,
    MAX_ASSESSMENT_REQUEST_BYTES,
    MAX_ASSESSMENT_RESPONSE_BYTES,
    ProtocolError,
    read_ndjson,
    validate_assessment_request,
    write_ndjson,
)


MAXIMUM_TOKENS = 384
DEPENDENCY_MISSING = "ASSESSMENT_VERIFIER_DEPENDENCY_MISSING"
CUDA_UNAVAILABLE = "ASSESSMENT_VERIFIER_CUDA_UNAVAILABLE"
MODEL_LOAD_FAILED = "ASSESSMENT_VERIFIER_MODEL_LOAD_FAILED"


class AssessmentRuntimeError(RuntimeError):
    """只攜帶Assessment verifier startup固定reason code。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


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
        max_length=MAXIMUM_TOKENS,
        truncation=True,
        padding=True,
        return_tensors="pt",
    ).to(next(model.parameters()).device)
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
        probabilities = score_options(
            model,
            tokenizer,
            entailment_index,
            checked["premise"],
            checked["options"],
        )
        write_ndjson(
            sys.stdout.buffer,
            {
                "schema": ASSESSMENT_RESPONSE_SCHEMA,
                "request_id": checked["request_id"],
                "entailment_probabilities": probabilities,
            },
            MAX_ASSESSMENT_RESPONSE_BYTES,
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

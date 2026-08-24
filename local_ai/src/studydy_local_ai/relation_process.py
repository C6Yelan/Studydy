"""以固定離線 mDeBERTa 驗證 grounded structural relation。"""

from __future__ import annotations

import gc
from pathlib import Path
import sys
from typing import Any

from .protocol import (
    MAX_RELATION_REQUEST_BYTES,
    MAX_RELATION_RESPONSE_BYTES,
    RELATION_RESPONSE_SCHEMA,
    RELATION_STARTUP_SCHEMA,
    ProtocolError,
    read_ndjson,
    validate_relation_request,
    write_ndjson,
)


HYPOTHESES = {
    "prerequisite": "Understanding B requires prior understanding of A.",
    "contains": "B is a subordinate concept, sub-concept, or component of A.",
}
ENTAILMENT_THRESHOLD = 0.8
MAXIMUM_TOKENS = 384

DEPENDENCY_MISSING = "RELATION_VERIFIER_DEPENDENCY_MISSING"
CUDA_UNAVAILABLE = "RELATION_VERIFIER_CUDA_UNAVAILABLE"
MODEL_LOAD_FAILED = "RELATION_VERIFIER_MODEL_LOAD_FAILED"


class RelationRuntimeError(RuntimeError):
    """只攜帶 verifier startup 的固定 reason code。"""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def load_relation_model(model_root: Path) -> tuple[Any, Any, dict[str, int]]:
    """只載入 runtime lock 固定的本機 safetensors checkpoint。"""

    try:
        import torch
        from transformers import (
            AutoConfig,
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )
    except ImportError:
        raise RelationRuntimeError(DEPENDENCY_MISSING) from None

    try:
        has_cuda = torch.cuda.is_available()
    except RuntimeError:
        has_cuda = False
    if not has_cuda:
        raise RelationRuntimeError(CUDA_UNAVAILABLE)

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
            model_root, local_files_only=True, trust_remote_code=False, use_fast=True
        )
        model = AutoModelForSequenceClassification.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        ).eval().cuda()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        raise RelationRuntimeError(MODEL_LOAD_FAILED) from None
    return model, tokenizer, labels


def _write_startup(*, reason_code: str | None = None) -> None:
    response = {
        "schema": RELATION_STARTUP_SCHEMA,
        "status": "ready" if reason_code is None else "failed",
    }
    if reason_code is not None:
        response["reason_code"] = reason_code
    write_ndjson(sys.stdout.buffer, response, MAX_RELATION_RESPONSE_BYTES)


def relation_is_entailed(
    model: Any,
    tokenizer: Any,
    label_ids: dict[str, int],
    premise: str,
    relation_type: str,
) -> bool:
    """D1 固定要求 entailment 達 threshold 且為 argmax。"""

    import torch

    tokens = tokenizer(
        premise,
        HYPOTHESES[relation_type],
        max_length=MAXIMUM_TOKENS,
        truncation=True,
        return_tensors="pt",
    ).to(next(model.parameters()).device)
    with torch.inference_mode():
        probabilities = torch.softmax(model(**tokens).logits[0], dim=-1)
    entailment = float(probabilities[label_ids["entailment"]])
    return (
        entailment >= ENTAILMENT_THRESHOLD
        and int(probabilities.argmax()) == label_ids["entailment"]
    )


def serve(model: Any, tokenizer: Any, label_ids: dict[str, int]) -> None:
    while True:
        request = read_ndjson(sys.stdin.buffer, MAX_RELATION_REQUEST_BYTES)
        if request is None:
            return
        checked = validate_relation_request(request)
        is_entailed = relation_is_entailed(
            model,
            tokenizer,
            label_ids,
            checked["premise"],
            checked["relation_type"],
        )
        write_ndjson(
            sys.stdout.buffer,
            {
                "schema": RELATION_RESPONSE_SCHEMA,
                "request_id": checked["request_id"],
                "outcome": "entailed" if is_entailed else "not_entailed",
            },
            MAX_RELATION_RESPONSE_BYTES,
        )


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    model = tokenizer = None
    try:
        model, tokenizer, label_ids = load_relation_model(Path(sys.argv[1]))
        _write_startup()
        serve(model, tokenizer, label_ids)
        return 0
    except RelationRuntimeError as error:
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

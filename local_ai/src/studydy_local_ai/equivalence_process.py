"""以固定離線 mDeBERTa 雙向驗證 Concept 等價。"""

from __future__ import annotations

import gc
from pathlib import Path
import sys
from typing import Any

from .protocol import (
    ProtocolError,
    read_ndjson,
    write_ndjson,
)
from .relation_process import RelationRuntimeError, load_relation_model


MAXIMUM_TOKENS = 384
ENTAILMENT_THRESHOLD = 0.8
EQUIVALENCE_REQUEST_SCHEMA = "local-concept-equivalence-request/v1"
EQUIVALENCE_RESPONSE_SCHEMA = "local-concept-equivalence-response/v1"
EQUIVALENCE_STARTUP_SCHEMA = "local-concept-equivalence-startup/v1"
MAX_EQUIVALENCE_REQUEST_BYTES = 64 * 1024
MAX_EQUIVALENCE_RESPONSE_BYTES = 2 * 1024


def validate_equivalence_request(request: Any) -> dict[str, str]:
    if (
        not isinstance(request, dict)
        or set(request) != {"schema", "request_id", "left_text", "right_text"}
        or request.get("schema") != EQUIVALENCE_REQUEST_SCHEMA
    ):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    checked = {}
    for field in ("left_text", "right_text"):
        value = request.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 24_576
            or any(
                ord(character) < 32 and character not in "\n\t"
                for character in value
            )
        ):
            raise ProtocolError("CHILD_REQUEST_INVALID")
        checked[field] = value
    request_id = request.get("request_id")
    if (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 128
        or any(
            not (character.isalnum() or character in "._-")
            for character in request_id
        )
    ):
        raise ProtocolError("CHILD_REQUEST_INVALID")
    return {"request_id": request_id, **checked}


def score_equivalence(
    model: Any,
    tokenizer: Any,
    label_ids: dict[str, int],
    left_text: str,
    right_text: str,
) -> dict[str, Any]:
    """同一 batch 計算 A→B 與 B→A，不截斷超長輸入。"""

    import torch

    tokens = tokenizer(
        [left_text, right_text],
        [right_text, left_text],
        padding=True,
        truncation=False,
        return_tensors="pt",
    )
    lengths = [int(length) for length in tokens["attention_mask"].sum(dim=1)]
    if any(length > MAXIMUM_TOKENS for length in lengths):
        return {
            "status": "unsupported",
            "reason_code": "VERIFIER_INPUT_TOO_LARGE",
            "token_lengths": lengths,
        }
    model_inputs = {
        name: value.to(next(model.parameters()).device)
        for name, value in tokens.items()
    }
    with torch.inference_mode():
        rows = torch.softmax(model(**model_inputs).logits, dim=-1).detach().cpu().tolist()
    labels_by_id = {index: label for label, index in label_ids.items()}

    def direction(probabilities: list[float], token_length: int) -> dict[str, Any]:
        return {
            "entailment_probability": probabilities[label_ids["entailment"]],
            "argmax_label": labels_by_id[
                max(range(len(probabilities)), key=probabilities.__getitem__)
            ],
            "token_length": token_length,
        }

    return {
        "status": "scored",
        "a_to_b": direction(rows[0], lengths[0]),
        "b_to_a": direction(rows[1], lengths[1]),
    }


def serve(model: Any, tokenizer: Any, label_ids: dict[str, int]) -> None:
    while True:
        request = read_ndjson(sys.stdin.buffer, MAX_EQUIVALENCE_REQUEST_BYTES)
        if request is None:
            return
        checked = validate_equivalence_request(request)
        scored = score_equivalence(
            model,
            tokenizer,
            label_ids,
            checked["left_text"],
            checked["right_text"],
        )
        write_ndjson(
            sys.stdout.buffer,
            {
                "schema": EQUIVALENCE_RESPONSE_SCHEMA,
                "request_id": checked["request_id"],
                **scored,
            },
            MAX_EQUIVALENCE_RESPONSE_BYTES,
        )


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    model = tokenizer = None
    try:
        model, tokenizer, label_ids = load_relation_model(Path(sys.argv[1]))
        write_ndjson(
            sys.stdout.buffer,
            {"schema": EQUIVALENCE_STARTUP_SCHEMA, "status": "ready"},
            MAX_EQUIVALENCE_RESPONSE_BYTES,
        )
        serve(model, tokenizer, label_ids)
        return 0
    except RelationRuntimeError as error:
        try:
            write_ndjson(
                sys.stdout.buffer,
                {
                    "schema": EQUIVALENCE_STARTUP_SCHEMA,
                    "status": "failed",
                    "reason_code": error.reason_code,
                },
                MAX_EQUIVALENCE_RESPONSE_BYTES,
            )
        except (OSError, ProtocolError):
            pass
        return 1
    except (OSError, ProtocolError, RuntimeError, TypeError, ValueError):
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

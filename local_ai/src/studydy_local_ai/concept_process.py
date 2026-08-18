"""以固定離線 Qwen 處理 text-only NDJSON request。"""

from __future__ import annotations

import gc
import json
import os
from pathlib import Path
import sys
from typing import Any

from .protocol import (
    CONCEPT_FAILURE_SCHEMA,
    CONCEPT_RESPONSE_SCHEMA,
    MAX_CONCEPT_REQUEST_BYTES,
    MAX_CONCEPT_RESPONSE_BYTES,
    MODEL_OUTPUT_TRUNCATED,
    ProtocolError,
    read_ndjson,
    validate_concept_request,
    write_ndjson,
)


PROMPT_TEMPLATE = """You extract study concepts from normalized text evidence.
Use only the supplied evidence. Return JSON only, with exactly this shape:
{"concepts":[{"label":"...","definition":"...","key_points":["..."],"evidence_ids":["..."]}]}
Every central claim and key point must be grounded by its listed Evidence IDs.
Do not return status, paths, coordinates, commentary, markdown, or additional fields."""
GENERATION_CONFIG = {"do_sample": False, "max_new_tokens": 1536, "use_cache": True}
INPUT_TOKEN_LIMIT = 4096


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def load_concept_model(model_root: Path) -> tuple[Any, Any]:
    """以 exact Qwen revision 的本機目錄及 BF16 載入。"""
    import torch
    from transformers import AutoTokenizer, Qwen3ForCausalLM, logging

    logging.set_verbosity_error()
    logging.disable_progress_bar()
    tokenizer = AutoTokenizer.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=False,
    )
    model = Qwen3ForCausalLM.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=False,
        dtype=torch.bfloat16,
    ).to("cuda").eval()
    return model, tokenizer


def run_concept(model: Any, tokenizer: Any, semantic_request: dict[str, Any]) -> str:
    """直接 tokenize 單一 user message，不先 render 再 retokenize。"""
    import torch

    user_text = f"{PROMPT_TEMPLATE}\nINPUT:\n{canonical_json(semantic_request)}"
    input_ids = output_ids = generated_ids = None
    try:
        input_ids = tokenizer.apply_chat_template(
            [{"role": "user", "content": user_text}],
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        )
        if input_ids.shape[-1] > INPUT_TOKEN_LIMIT:
            raise ProtocolError("MODEL_INPUT_TOO_LARGE")
        input_ids = input_ids.to(model.device)
        with torch.inference_mode():
            output_ids = model.generate(input_ids=input_ids, **GENERATION_CONFIG)
        generated_ids = output_ids[0, input_ids.shape[-1] :]
        eos_token_ids = model.generation_config.eos_token_id
        if type(eos_token_ids) is int:
            eos_token_ids = [eos_token_ids]
        if (
            not isinstance(eos_token_ids, (list, tuple))
            or not eos_token_ids
            or any(type(token_id) is not int for token_id in eos_token_ids)
            or generated_ids.shape[-1] == 0
            or int(generated_ids[-1]) not in eos_token_ids
        ):
            raise ProtocolError(MODEL_OUTPUT_TRUNCATED)
        return tokenizer.decode(generated_ids, skip_special_tokens=True)
    finally:
        del generated_ids
        del output_ids
        del input_ids


def serve(model: Any, tokenizer: Any) -> None:
    """每次 request 明列 attempt；raw model text 只存在 anonymous pipe。"""
    import torch

    while True:
        request = read_ndjson(sys.stdin.buffer, MAX_CONCEPT_REQUEST_BYTES)
        if request is None:
            return
        validated = validate_concept_request(request)
        model_text = None
        try:
            model_text = run_concept(model, tokenizer, validated["semantic_request"])
            response = {
                "schema": CONCEPT_RESPONSE_SCHEMA,
                "request_id": validated["request_id"],
                "attempt": validated["attempt"],
                "model_text": model_text,
            }
        except torch.OutOfMemoryError:
            response = {
                "schema": CONCEPT_FAILURE_SCHEMA,
                "request_id": validated["request_id"],
                "attempt": validated["attempt"],
                "reason_code": "MODEL_OOM",
            }
        except ProtocolError as error:
            response = {
                "schema": CONCEPT_FAILURE_SCHEMA,
                "request_id": validated["request_id"],
                "attempt": validated["attempt"],
                "reason_code": str(error),
            }
        except RuntimeError:
            response = {
                "schema": CONCEPT_FAILURE_SCHEMA,
                "request_id": validated["request_id"],
                "attempt": validated["attempt"],
                "reason_code": "MODEL_GENERATION_FAILED",
            }
        finally:
            gc.collect()
            torch.cuda.empty_cache()
        write_ndjson(sys.stdout.buffer, response, MAX_CONCEPT_RESPONSE_BYTES)
        del response
        del model_text
        del validated
        del request


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        }
    )
    model = tokenizer = None
    try:
        model, tokenizer = load_concept_model(Path(sys.argv[1]))
        serve(model, tokenizer)
        return 0
    except (ProtocolError, OSError, RuntimeError, ValueError):
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

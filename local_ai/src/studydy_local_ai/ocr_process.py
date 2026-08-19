"""以固定離線 Unlimited-OCR 處理頁面 NDJSON request。"""

from __future__ import annotations

import gc
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

from .protocol import (
    MAX_OCR_REQUEST_BYTES,
    MAX_OCR_RESPONSE_BYTES,
    OCR_RESPONSE_SCHEMA,
    ProtocolError,
    read_ndjson,
    validate_ocr_request,
    write_ndjson,
)


OCR_PROMPT = "<image>document parsing."
DET_BLOCK = re.compile(
    r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^\]]+\])\s*<\|/det\|>(.*?)(?=<\|det\|>|\Z)",
    re.DOTALL,
)


def normalize_candidate_text(text: str) -> str:
    """只做鎖定的換行、NFC 與行尾空白正規化。"""
    normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def parse_det_blocks(model_text: str) -> list[dict[str, Any]]:
    """嚴格解析完整 det blocks；不搜尋或修補 marker 外文字。"""
    normalized = normalize_candidate_text(model_text)
    blocks: list[dict[str, Any]] = []
    cursor = 0
    for match in DET_BLOCK.finditer(normalized):
        if normalized[cursor : match.start()].strip():
            raise ProtocolError("OCR_OUTPUT_INVALID")
        try:
            bbox = json.loads(match.group(2))
        except ValueError as error:
            raise ProtocolError("OCR_OUTPUT_INVALID") from error
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or any(type(number) not in {int, float} or not math.isfinite(number) for number in bbox)
            or not (0 <= bbox[0] < bbox[2] <= 1000 and 0 <= bbox[1] < bbox[3] <= 1000)
        ):
            raise ProtocolError("OCR_OUTPUT_INVALID")
        text = match.group(3)
        blocks.append({"type": match.group(1), "bbox": bbox, "text": text})
        cursor = match.end()
    if (
        not blocks
        or normalized[cursor:].strip()
        or normalized.count("<|det|>") != len(blocks)
        or normalized.count("<|/det|>") != len(blocks)
    ):
        raise ProtocolError("OCR_OUTPUT_INVALID")
    return blocks


def load_ocr_model(model_root: Path) -> tuple[Any, Any]:
    """只從固定本機 snapshot 載入 Unlimited-OCR。"""
    import torch
    from transformers import AutoModel, AutoTokenizer, logging

    logging.set_verbosity_error()
    logging.disable_progress_bar()
    tokenizer = AutoTokenizer.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
    )
    model = AutoModel.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
    ).eval().cuda()
    return model, tokenizer


def run_ocr(model: Any, tokenizer: Any, png_bytes: bytes) -> str:
    """以 memory-only PIL image 呼叫 exact unlimited_r1 inference。"""
    from PIL import Image

    image = Image.open(BytesIO(png_bytes))
    image.load()
    model_module = sys.modules[model.__class__.__module__]
    original_loader = model_module.load_pil_images
    original_makedirs = model_module.os.makedirs
    try:
        model_module.load_pil_images = lambda _: [image.copy()]
        model_module.os.makedirs = lambda *args, **kwargs: None
        return model.infer(
            tokenizer,
            prompt=OCR_PROMPT,
            image_file="memory-only",
            output_path="memory-only",
            base_size=1024,
            image_size=640,
            crop_mode=True,
            eval_mode=True,
            max_length=32768,
            no_repeat_ngram_size=35,
            ngram_window=128,
            temperature=0.0,
            save_results=False,
        )
    finally:
        model_module.load_pil_images = original_loader
        model_module.os.makedirs = original_makedirs
        image.close()


def serve(model: Any, tokenizer: Any) -> None:
    """載入一次後循序處理 pages；invalid output 令 child fail closed。"""
    while True:
        request = read_ndjson(sys.stdin.buffer, MAX_OCR_REQUEST_BYTES)
        if request is None:
            return
        validated = validate_ocr_request(request)
        if hashlib.sha256(validated["png_bytes"]).hexdigest() != validated["render_sha256"]:
            raise ProtocolError("CHILD_REQUEST_INVALID")
        model_text = run_ocr(model, tokenizer, validated["png_bytes"])
        if not isinstance(model_text, str):
            raise ProtocolError("OCR_OUTPUT_INVALID")
        blocks = parse_det_blocks(model_text)
        write_ndjson(
            sys.stdout.buffer,
            {
                "schema": OCR_RESPONSE_SCHEMA,
                "request_id": validated["request_id"],
                "blocks": blocks,
            },
            MAX_OCR_RESPONSE_BYTES,
        )


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
        model, tokenizer = load_ocr_model(Path(sys.argv[1]))
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

import os
import sys
from types import SimpleNamespace

import pytest

from studydy_local_ai.ocr_process import OCR_PROMPT, parse_det_blocks, run_ocr
from studydy_local_ai.protocol import ProtocolError


def test_exact_p02_det_grammar_preserves_type_text_and_bbox():
    blocks = parse_det_blocks(
        "<|det|>title [100,200,900,300]<|/det|>Public heading\n"
        "<|det|>text [100,350,900,500]<|/det|>Public body"
    )
    assert blocks == [
        {"type": "title", "bbox": [100, 200, 900, 300], "text": "Public heading\n"},
        {"type": "text", "bbox": [100, 350, 900, 500], "text": "Public body"},
    ]


def test_det_grammar_uses_response_bytes_instead_of_fixed_block_count():
    model_text = "".join(
        f"<|det|>code [1,2,3,4]<|/det|>  line {index}\n    indented\n"
        for index in range(65)
    )
    blocks = parse_det_blocks(model_text)
    assert len(blocks) == 65
    assert blocks[0]["text"] == "  line 0\n    indented\n"


@pytest.mark.parametrize(
    "model_text",
    [
        "plain output",
        "prefix<|det|>text [1,2,3,4]<|/det|>x",
        "<|ref|>text<|/ref|><|det|>text [1,2,3,4]<|/det|>x",
        "<|det|>text [1,2,3]<|/det|>x",
        "<|det|>text [4,2,1,3]<|/det|>x",
        "<|det|>text [-1,2,3,4]<|/det|>x",
        "<|det|>text [1,2,1001,4]<|/det|>x",
        "<|det|>text [1,2,3,4]x",
    ],
)
def test_invalid_or_non_det_output_fails_closed(model_text):
    with pytest.raises(ProtocolError, match="OCR_OUTPUT_INVALID"):
        parse_det_blocks(model_text)


def test_ocr_infer_uses_exact_unlimited_r1_arguments(monkeypatch):
    calls = []

    class FakeImage:
        def load(self):
            return None

        def copy(self):
            return self

        def close(self):
            return None

    class FakeModel:
        def infer(self, tokenizer, **arguments):
            calls.append((tokenizer, arguments))
            return "<|det|>text [1,2,3,4]<|/det|>Public"

    monkeypatch.setitem(
        sys.modules,
        FakeModel.__module__,
        SimpleNamespace(load_pil_images=lambda _: [], os=SimpleNamespace(makedirs=os.makedirs)),
    )
    monkeypatch.setitem(
        sys.modules,
        "PIL",
        SimpleNamespace(Image=SimpleNamespace(open=lambda _: FakeImage())),
    )
    assert run_ocr(FakeModel(), "tokenizer", b"public image").startswith("<|det|>")
    assert OCR_PROMPT == "<image>document parsing."
    assert calls == [
        (
            "tokenizer",
            {
                "prompt": "<image>document parsing.",
                "image_file": "memory-only",
                "output_path": "memory-only",
                "base_size": 1024,
                "image_size": 640,
                "crop_mode": True,
                "eval_mode": True,
                "max_length": 32768,
                "no_repeat_ngram_size": 35,
                "ngram_window": 128,
                "temperature": 0.0,
                "save_results": False,
            },
        )
    ]

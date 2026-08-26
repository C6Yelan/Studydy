from contextlib import nullcontext
from io import BytesIO
import json
from types import SimpleNamespace
import sys

import pytest

from studydy_local_ai.assessment_process import (
    ASSESSMENT_RESPONSE_SCHEMA,
    INPUT_TOO_LARGE,
    MAXIMUM_TOKENS,
    AssessmentInputTooLarge,
    score_options,
    serve,
)
import studydy_local_ai.assessment_process as assessment_process


class _Tokens(dict):
    def to(self, _device):
        return self


class _Rows(list):
    def cpu(self):
        return self


def test_scores_exactly_four_options_with_entailment_probability(monkeypatch):
    calls = []
    rows = _Rows(
        [
            [0.9, 0.05, 0.05],
            [0.1, 0.8, 0.1],
            [0.2, 0.3, 0.5],
            [0.4, 0.3, 0.3],
        ]
    )

    class Model:
        def parameters(self):
            return iter([SimpleNamespace(device="cuda")])

        def __call__(self, **tokens):
            assert tokens == {
                "input": "tokens",
                "input_ids": SimpleNamespace(shape=(4, MAXIMUM_TOKENS)),
            }
            return SimpleNamespace(logits=rows)

    def tokenizer(premises, options, **settings):
        calls.append((premises, options, settings))
        return _Tokens(
            input="tokens",
            input_ids=SimpleNamespace(shape=(4, MAXIMUM_TOKENS)),
        )

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            inference_mode=nullcontext,
            softmax=lambda values, dim: values,
        ),
    )
    assert score_options(
        Model(), tokenizer, 0, "Exact Evidence", ["A", "B", "C", "D"]
    ) == [0.9, 0.1, 0.2, 0.4]
    assert calls == [
        (
            ["Exact Evidence"] * 4,
            ["A", "B", "C", "D"],
            {
                "truncation": False,
                "padding": True,
                "return_tensors": "pt",
            },
        )
    ]


def test_rejects_complete_pair_over_token_boundary_before_model_inference(
    monkeypatch,
):
    class Model:
        def parameters(self):
            return iter([SimpleNamespace(device="cuda")])

        def __call__(self, **_tokens):
            raise AssertionError("over-limit input must not reach inference")

    def tokenizer(*_args, **settings):
        assert settings["truncation"] is False
        return _Tokens(
            input_ids=SimpleNamespace(shape=(4, MAXIMUM_TOKENS + 1))
        )

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())

    with pytest.raises(AssessmentInputTooLarge):
        score_options(
            Model(), tokenizer, 0, "long Evidence", ["A", "B", "C", "D"]
        )


def test_child_protocol_returns_explicit_over_limit_rejection(monkeypatch):
    request = {
        "schema": "local-assessment-verifier-request/v1",
        "request_id": "long-evidence",
        "premise": "complete Evidence",
        "options": ["A", "B", "C", "D"],
    }
    input_stream = BytesIO(
        json.dumps(request, separators=(",", ":")).encode() + b"\n"
    )
    output_stream = BytesIO()
    monkeypatch.setattr(
        assessment_process,
        "score_options",
        lambda *_: (_ for _ in ()).throw(AssessmentInputTooLarge()),
    )
    monkeypatch.setattr(
        assessment_process,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(buffer=input_stream),
            stdout=SimpleNamespace(buffer=output_stream),
        ),
    )

    serve(None, None, 0)

    assert json.loads(output_stream.getvalue()) == {
        "schema": ASSESSMENT_RESPONSE_SCHEMA,
        "request_id": "long-evidence",
        "status": "rejected",
        "reason_code": INPUT_TOO_LARGE,
    }

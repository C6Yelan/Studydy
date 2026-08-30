from contextlib import nullcontext
from types import SimpleNamespace
import sys

import pytest

from studydy_local_ai.equivalence_process import (
    score_equivalence,
    validate_equivalence_request,
)
from studydy_local_ai.protocol import ProtocolError


class _Column:
    def __init__(self, values):
        self.values = values

    def sum(self, dim):
        assert dim == 1
        return self.values

    def to(self, _device):
        return self


class _Value:
    def __init__(self, value):
        self.value = value

    def to(self, _device):
        return self


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.rows


def test_equivalence_scores_both_directions_without_truncation(monkeypatch):
    calls = []

    class Model:
        def parameters(self):
            return iter([SimpleNamespace(device="cuda")])

        def __call__(self, **_tokens):
            return SimpleNamespace(logits="logits")

    def tokenizer(left, right, **options):
        calls.append((left, right, options))
        return {
            "attention_mask": _Column([120, 121]),
            "input_ids": _Value("tokens"),
        }

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            inference_mode=nullcontext,
            softmax=lambda logits, dim: _Rows(
                [[0.91, 0.05, 0.04], [0.87, 0.08, 0.05]]
            ),
        ),
    )
    result = score_equivalence(
        Model(), tokenizer, {"entailment": 0, "neutral": 1, "contradiction": 2},
        "Concept A", "Concept B",
    )

    assert result["status"] == "scored"
    assert result["a_to_b"]["entailment_probability"] == 0.91
    assert result["b_to_a"]["entailment_probability"] == 0.87
    assert calls == [(
        ["Concept A", "Concept B"],
        ["Concept B", "Concept A"],
        {"padding": True, "truncation": False, "return_tensors": "pt"},
    )]


def test_equivalence_request_accepts_only_two_bounded_texts():
    request = {
        "schema": "local-concept-equivalence-request/v1",
        "request_id": "pair-1",
        "left_text": "Concept A",
        "right_text": "Concept B",
    }
    assert validate_equivalence_request(request) == {
        "request_id": "pair-1",
        "left_text": "Concept A",
        "right_text": "Concept B",
    }
    for invalid in (
        {**request, "left_text": ""},
        {**request, "right_text": "x" * 24_577},
        {**request, "pair_id": "private"},
    ):
        with pytest.raises(ProtocolError, match="CHILD_REQUEST_INVALID"):
            validate_equivalence_request(invalid)


def test_equivalence_overflow_vetoes_before_model_call(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())

    class Model:
        def __call__(self, **_tokens):
            raise AssertionError("overflow must not call model")

    def tokenizer(*_args, **_options):
        return {
            "attention_mask": _Column([385, 20]),
            "input_ids": _Value("tokens"),
        }

    result = score_equivalence(
        Model(), tokenizer, {"entailment": 0, "neutral": 1, "contradiction": 2},
        "Concept A", "Concept B",
    )
    assert result == {
        "status": "unsupported",
        "reason_code": "VERIFIER_INPUT_TOO_LARGE",
        "token_lengths": [385, 20],
    }

from contextlib import nullcontext
from types import SimpleNamespace
import sys

from studydy_local_ai.assessment_process import MAXIMUM_TOKENS, score_options


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
            assert tokens == {"input": "tokens"}
            return SimpleNamespace(logits=rows)

    def tokenizer(premises, options, **settings):
        calls.append((premises, options, settings))
        return _Tokens(input="tokens")

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
                "max_length": MAXIMUM_TOKENS,
                "truncation": True,
                "padding": True,
                "return_tensors": "pt",
            },
        )
    ]

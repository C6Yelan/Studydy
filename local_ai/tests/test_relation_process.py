from contextlib import nullcontext
from types import SimpleNamespace
import sys

import pytest

from studydy_local_ai.relation_process import (
    CUDA_UNAVAILABLE,
    DEPENDENCY_MISSING,
    ENTAILMENT_THRESHOLD,
    HYPOTHESES,
    MAXIMUM_TOKENS,
    MODEL_LOAD_FAILED,
    RelationRuntimeError,
    load_relation_model,
    relation_is_entailed,
)


class _Probabilities:
    def __init__(self, values):
        self.values = values

    def __getitem__(self, index):
        return self.values[index]

    def argmax(self):
        return max(range(len(self.values)), key=self.values.__getitem__)


class _Tokens(dict):
    def to(self, _device):
        return self


@pytest.mark.parametrize(
    ("probabilities", "expected"),
    [([0.81, 0.10, 0.09], True), ([0.79, 0.11, 0.10], False), ([0.80, 0.90, 0.01], False)],
)
def test_d1_requires_entailment_threshold_and_argmax(monkeypatch, probabilities, expected):
    calls = []

    class Model:
        def parameters(self):
            return iter([SimpleNamespace(device="cuda")])

        def __call__(self, **tokens):
            assert tokens == {"input": "tokens"}
            return SimpleNamespace(logits=[probabilities])

    def tokenizer(premise, hypothesis, **options):
        calls.append((premise, hypothesis, options))
        return _Tokens(input="tokens")

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            inference_mode=nullcontext,
            softmax=lambda values, dim: _Probabilities(values),
        ),
    )
    assert relation_is_entailed(
        Model(), tokenizer, {"entailment": 0, "neutral": 1, "contradiction": 2},
        "A: public premise\nB: public target", "prerequisite"
    ) is expected
    assert calls == [(
        "A: public premise\nB: public target",
        HYPOTHESES["prerequisite"],
        {"max_length": MAXIMUM_TOKENS, "truncation": True, "return_tensors": "pt"},
    )]
    assert ENTAILMENT_THRESHOLD == 0.8


def test_relation_runtime_reports_dependency_before_cuda(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    with pytest.raises(RelationRuntimeError) as failure:
        load_relation_model(tmp_path)

    assert failure.value.reason_code == DEPENDENCY_MISSING


def test_relation_runtime_distinguishes_cuda_from_model_load(monkeypatch, tmp_path):
    class UnusedLoader:
        pass

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoConfig=UnusedLoader,
            AutoModelForSequenceClassification=UnusedLoader,
            AutoTokenizer=UnusedLoader,
        ),
    )
    with pytest.raises(RelationRuntimeError) as cuda_failure:
        load_relation_model(tmp_path)
    assert cuda_failure.value.reason_code == CUDA_UNAVAILABLE

    class FailingLoader:
        @staticmethod
        def from_pretrained(*_args, **_kwargs):
            raise OSError("private diagnostic")

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True)),
    )
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(
            AutoConfig=FailingLoader,
            AutoModelForSequenceClassification=FailingLoader,
            AutoTokenizer=FailingLoader,
        ),
    )
    with pytest.raises(RelationRuntimeError) as model_failure:
        load_relation_model(tmp_path)
    assert model_failure.value.reason_code == MODEL_LOAD_FAILED

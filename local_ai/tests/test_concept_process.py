import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import studydy_local_ai.concept_process as concept_module
from studydy_local_ai.concept_process import (
    GENERATION_CONFIG,
    PROMPT_TEMPLATE,
    canonical_json,
    run_concept,
)
from studydy_local_ai.protocol import ProtocolError


FIXTURES = Path(__file__).parent / "fixtures"


class FakeInferenceMode:
    def __enter__(self):
        return None

    def __exit__(self, *arguments):
        return False


class FakeOutOfMemoryError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def fake_torch(monkeypatch):
    fake = SimpleNamespace(
        inference_mode=lambda: FakeInferenceMode(),
        cuda=SimpleNamespace(empty_cache=lambda: None),
        OutOfMemoryError=FakeOutOfMemoryError,
    )
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake,
    )
    return fake


class FakeInputIds:
    def __init__(self, tokens=100):
        self.shape = (1, tokens)

    def to(self, device):
        assert device == "cuda"
        return self


class FakeGeneratedIds:
    def __init__(self, tokens):
        self.tokens = tokens
        self.shape = (len(tokens),)

    def __getitem__(self, key):
        return self.tokens[key]


class FakeOutputIds:
    def __init__(self, generated_tokens):
        self.generated_tokens = generated_tokens

    def __getitem__(self, key):
        assert key == (0, slice(100, None, None))
        return FakeGeneratedIds(self.generated_tokens)


class FakeTokenizer:
    def __init__(self, tokens=100, model_text='{"concepts":[]}'):
        self.tokens = tokens
        self.model_text = model_text
        self.calls = []
        self.decode_calls = 0

    def apply_chat_template(self, messages, **arguments):
        self.calls.append((messages, arguments))
        return FakeInputIds(self.tokens)

    def decode(self, generated_ids, *, skip_special_tokens):
        assert isinstance(generated_ids, FakeGeneratedIds)
        assert skip_special_tokens is True
        self.decode_calls += 1
        return self.model_text


class FakeModel:
    device = "cuda"

    def __init__(self, generated_tokens=None):
        self.calls = []
        self.generated_tokens = generated_tokens or [7, 99]
        self.generation_config = SimpleNamespace(eos_token_id=99)

    def generate(self, **arguments):
        self.calls.append(arguments)
        return FakeOutputIds(self.generated_tokens)


def _request():
    return json.loads((FIXTURES / "semantic_request.json").read_text(encoding="utf-8"))


def test_prompt_and_model_visible_request_match_p02_exact_bytes():
    assert hashlib.sha256(PROMPT_TEMPLATE.encode("utf-8")).hexdigest() == (
        "97f14f58b3599f22fcda7921d69fbd64035562c11897a4eadc6aacb355f5ca5c"
    )
    tokenizer = FakeTokenizer()
    model = FakeModel()
    run_concept(model, tokenizer, _request())
    messages, arguments = tokenizer.calls[0]
    assert messages == [
        {"role": "user", "content": f"{PROMPT_TEMPLATE}\nINPUT:\n{canonical_json(_request())}"}
    ]
    assert arguments == {
        "tokenize": True,
        "add_generation_prompt": True,
        "return_tensors": "pt",
    }
    assert len(model.calls) == 1
    assert model.calls[0]["input_ids"].shape == (1, 100)
    assert {key: model.calls[0][key] for key in GENERATION_CONFIG} == GENERATION_CONFIG
    assert GENERATION_CONFIG == {
        "do_sample": False,
        "max_new_tokens": 1536,
        "use_cache": True,
    }


def test_model_input_over_4096_tokens_fails_before_generation():
    tokenizer = FakeTokenizer(tokens=4097)
    model = FakeModel()
    with pytest.raises(ProtocolError, match="MODEL_INPUT_TOO_LARGE"):
        run_concept(model, tokenizer, _request())
    assert model.calls == []


def test_non_eos_cap_hit_rejects_even_when_decoded_prefix_would_be_valid_json():
    tokenizer = FakeTokenizer(model_text='{"concepts":[]}')
    model = FakeModel(generated_tokens=[7] * 1_536)
    with pytest.raises(ProtocolError, match="MODEL_OUTPUT_TRUNCATED"):
        run_concept(model, tokenizer, _request())
    assert tokenizer.decode_calls == 0


def test_below_cap_final_eos_returns_decoded_output():
    tokenizer = FakeTokenizer(model_text='{"concepts":[]}')
    model = FakeModel(generated_tokens=[7, 99])
    assert run_concept(model, tokenizer, _request()) == '{"concepts":[]}'
    assert tokenizer.decode_calls == 1


class TrackedInputIds:
    def __init__(self, events, tokens=100):
        self.events = events
        self.shape = (1, tokens)

    def to(self, device):
        assert device == "cuda"
        return self

    def __del__(self):
        self.events.append("input_deleted")


class TrackedGeneratedIds:
    def __init__(self, events, tokens):
        self.events = events
        self.tokens = tokens
        self.shape = (len(tokens),)

    def __getitem__(self, key):
        return self.tokens[key]

    def __del__(self):
        self.events.append("generated_deleted")


class TrackedOutputIds:
    def __init__(self, events, generated_tokens):
        self.events = events
        self.generated_tokens = generated_tokens

    def __getitem__(self, key):
        assert key == (0, slice(100, None, None))
        return TrackedGeneratedIds(self.events, self.generated_tokens)

    def __del__(self):
        self.events.append("output_deleted")


class TrackedTokenizer:
    def __init__(self, events, token_counts=None):
        self.events = events
        self.token_counts = iter(token_counts or [100])

    def apply_chat_template(self, messages, **arguments):
        return TrackedInputIds(self.events, next(self.token_counts))

    def decode(self, generated_ids, *, skip_special_tokens):
        assert isinstance(generated_ids, TrackedGeneratedIds)
        assert skip_special_tokens is True
        self.events.append("decode")
        return '{"concepts":[]}'


class TrackedModel:
    device = "cuda"

    def __init__(self, events, failures=None, generated_tokens=None):
        self.events = events
        self.failures = iter(failures or [])
        self.generated_tokens = generated_tokens or [7, 99]
        self.generation_config = SimpleNamespace(eos_token_id=99)
        self.calls = 0

    def generate(self, **arguments):
        self.calls += 1
        self.events.append(f"generate_{self.calls}")
        failure = next(self.failures, None)
        if failure is not None:
            raise failure("public generation failure")
        return TrackedOutputIds(self.events, self.generated_tokens)


def _concept_request(attempt):
    return {
        "schema": "local-concept-request/v1",
        "request_id": "public-semantic",
        "attempt": attempt,
        "semantic_request": _request(),
    }


def _serve_requests(monkeypatch, requests, model, tokenizer, events):
    pending = iter([*requests, None])
    responses = []
    monkeypatch.setattr(concept_module, "read_ndjson", lambda stream, limit: next(pending))
    monkeypatch.setattr(
        concept_module,
        "write_ndjson",
        lambda stream, response, limit: (events.append("write"), responses.append(dict(response))),
    )
    monkeypatch.setattr(concept_module.gc, "collect", lambda: events.append("gc"))
    monkeypatch.setattr(concept_module.sys, "stdin", SimpleNamespace(buffer=object()))
    monkeypatch.setattr(concept_module.sys, "stdout", SimpleNamespace(buffer=object()))
    concept_module.serve(model, tokenizer)
    return responses


def test_success_releases_tensors_and_allocator_cache_before_response_write(
    monkeypatch, fake_torch
):
    events = []
    fake_torch.cuda.empty_cache = lambda: events.append("empty_cache")
    model = TrackedModel(events)
    responses = _serve_requests(
        monkeypatch,
        [_concept_request(1)],
        model,
        TrackedTokenizer(events),
        events,
    )
    assert responses[0]["model_text"] == '{"concepts":[]}'
    assert events == [
        "generate_1",
        "decode",
        "generated_deleted",
        "output_deleted",
        "input_deleted",
        "gc",
        "empty_cache",
        "write",
    ]


def test_non_eos_output_never_enters_success_response(monkeypatch, fake_torch):
    events = []
    fake_torch.cuda.empty_cache = lambda: events.append("empty_cache")
    model = TrackedModel(events, generated_tokens=[7] * 1_536)
    responses = _serve_requests(
        monkeypatch,
        [_concept_request(1)],
        model,
        TrackedTokenizer(events),
        events,
    )
    assert responses == [
        {
            "schema": "local-concept-failure/v1",
            "request_id": "public-semantic",
            "attempt": 1,
            "reason_code": "MODEL_OUTPUT_TRUNCATED",
        }
    ]
    assert "decode" not in events
    assert events[-3:] == ["gc", "empty_cache", "write"]


def test_input_cap_and_generation_failure_cleanup_before_each_response(
    monkeypatch, fake_torch
):
    events = []
    fake_torch.cuda.empty_cache = lambda: events.append("empty_cache")
    model = TrackedModel(events, failures=[RuntimeError])
    responses = _serve_requests(
        monkeypatch,
        [_concept_request(1), _concept_request(2)],
        model,
        TrackedTokenizer(events, token_counts=[4097, 100]),
        events,
    )
    assert [response["reason_code"] for response in responses] == [
        "MODEL_INPUT_TOO_LARGE",
        "MODEL_GENERATION_FAILED",
    ]
    assert model.calls == 1
    assert events.count("input_deleted") == 2
    assert events.count("gc") == events.count("empty_cache") == events.count("write") == 2
    assert events.index("empty_cache") < events.index("write")


def test_oom_retry_reuses_model_after_cleanup_and_then_returns_same_output(
    monkeypatch, fake_torch
):
    events = []
    fake_torch.cuda.empty_cache = lambda: events.append("empty_cache")
    model = TrackedModel(events, failures=[FakeOutOfMemoryError])
    responses = _serve_requests(
        monkeypatch,
        [_concept_request(1), _concept_request(2)],
        model,
        TrackedTokenizer(events, token_counts=[100, 100]),
        events,
    )
    assert responses[0]["reason_code"] == "MODEL_OOM"
    assert responses[1]["model_text"] == '{"concepts":[]}'
    assert model.calls == 2
    assert events.count("input_deleted") == 2
    assert events.count("empty_cache") == events.count("write") == 2
    first_write = events.index("write")
    assert events.index("empty_cache") < first_write < events.index("generate_2")

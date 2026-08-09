from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import socket
import urllib.error

import pytest

from pdf_evidence import page_structure_generation
from pdf_evidence.page_structure import validate_page_structure
from pdf_evidence.page_structure_generation import (
    finalize_page_structure,
    generate_page_structure,
)


class _Response:
    """提供 urllib 測試所需的最小 HTTP response。"""

    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self.body

    def close(self):
        return None


class _Opener:
    """將 direct opener 的 open 呼叫交給測試 callback。"""

    def __init__(self, callback):
        self.callback = callback

    def open(self, request, timeout):
        return self.callback(request, timeout)


def _render_bytes():
    """建立不需真實模型處理的 synthetic PNG bytes。"""
    return b"\x89PNG\r\n\x1a\nsynthetic-page"


def _bind_evidence(evidence):
    """依固定 hash 公式更新 material、page 與 evidence references。"""
    source = evidence["hashes"]["source_sha256"]
    native = evidence["hashes"]["native_sha256"]
    render = evidence["hashes"]["render_sha256"]
    page_number = evidence["page_number"]
    page_hash = hashlib.sha256(f"{source}:{page_number}".encode("ascii")).hexdigest()
    evidence_hash = hashlib.sha256(
        f"{source}:{page_number}:{native}:{render}".encode("ascii")
    ).hexdigest()
    evidence["material_ref"] = f"material:sha256:{source}"
    evidence["page_ref"] = f"page:sha256:{page_hash}"
    evidence["evidence_ref"] = f"evidence:sha256:{evidence_hash}"


def _page_evidence(render_bytes=None):
    """建立包含 render 與座標 binding 的成功 Page Evidence。"""
    render_bytes = _render_bytes() if render_bytes is None else render_bytes
    evidence = {
        "schema": "page-evidence/v1",
        "status": "succeeded",
        "page_number": 1,
        "hashes": {
            "source_sha256": "a" * 64,
            "native_sha256": "b" * 64,
            "render_sha256": hashlib.sha256(render_bytes).hexdigest(),
        },
        "render": {
            "schema": "page-render/v1",
            "width_pixels": 400,
            "height_pixels": 200,
        },
        "geometry": {"visible_points": [0.0, 0.0, 200.0, 100.0]},
        "coordinate_transform": {
            "native_coordinate_space": "unrotated_page_points",
            "render_coordinate_space": "rotated_page_points",
            "rotated_to_point": [0.0, -1.0, 1.0, 0.0, 0.0, 200.0],
        },
    }
    _bind_evidence(evidence)
    return evidence


def _config(cache_dir):
    """建立完整且不含 secret 的本機 runtime 設定。"""
    return {
        "endpoint_url": "http://127.0.0.1:8080",
        "cache_dir": str(cache_dir),
        "deadline_seconds": 10,
        "max_attempts": 2,
        "retry_backoff_seconds": 0,
        "model_id": "local-page-model",
        "model_revision": "revision-1",
        "model_artifact_sha256": "d" * 64,
        "projector_sha256": "e" * 64,
        "runtime_id": "runtime-1",
        "processing_policy_version": "page-structure-generation-policy/v2",
    }


def _model_body():
    """建立模型應回傳的 normalized Page Structure body。"""
    return {
        "elements": [
            {
                "id": "heading-1",
                "type": "heading",
                "bbox": [100, 100, 400, 300],
                "text": "Visible heading",
            }
        ],
        "reading_order": ["heading-1"],
        "spatial_relations": [],
    }


def _outer_response(model_body=None, finish_reason="stop"):
    """包裝本機 chat completions 的 JSON response。"""
    model_body = _model_body() if model_body is None else model_body
    return json.dumps(
        {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": json.dumps(model_body)},
                }
            ]
        }
    ).encode("utf-8")


def _successful_build_opener(calls, handlers_seen=None):
    """記錄 direct opener 與 request，再回傳固定模型結果。"""
    def open_request(request, timeout):
        calls.append((request, timeout))
        return _Response(_outer_response())

    def build_opener(*handlers):
        if handlers_seen is not None:
            handlers_seen.extend(handlers)
        return _Opener(open_request)

    return build_opener


def test_generate_page_structure_success_and_binding(tmp_path, monkeypatch):
    """驗證 request 最小化、座標 binding、cache 寫入與重新驗證。"""
    render_bytes = _render_bytes()
    evidence = _page_evidence(render_bytes)
    original_evidence = deepcopy(evidence)
    config = _config(tmp_path / "cache")
    config["endpoint_url"] = "http://localhost:8080"
    calls = []
    handlers = []
    monkeypatch.setattr(
        page_structure_generation.urllib.request,
        "build_opener",
        _successful_build_opener(calls, handlers),
    )

    mismatch = generate_page_structure(evidence, render_bytes + b"changed", config)
    assert mismatch == {
        "processing": "failed",
        "reason_code": "RENDER_HASH_MISMATCH",
        "input_evidence_ref": evidence["evidence_ref"],
    }
    assert calls == []
    assert not Path(config["cache_dir"]).exists()

    result = generate_page_structure(evidence, render_bytes, config)

    assert result["processing"] == "succeeded"
    assert result["reason_code"] == "PAGE_STRUCTURE_READY"
    assert set(result) == {
        "processing",
        "reason_code",
        "cache_key",
        "input_evidence_ref",
        "runtime_identity",
        "page_structure",
    }
    assert set(result["runtime_identity"]) == {
        "model_id",
        "model_revision",
        "model_artifact_sha256",
        "projector_sha256",
        "runtime_id",
        "prompt_version",
        "processing_policy_version",
    }
    assert result["runtime_identity"]["prompt_version"] == "page-structure-prompt/v4"
    assert (
        result["runtime_identity"]["processing_policy_version"]
        == "page-structure-generation-policy/v2"
    )
    structure = result["page_structure"]
    assert structure["schema"] == "page-structure/v1"
    assert structure["material_ref"] == evidence["material_ref"]
    assert structure["page_ref"] == evidence["page_ref"]
    assert structure["page_number"] == evidence["page_number"]
    assert structure["input_evidence_ref"] == evidence["evidence_ref"]
    assert structure["coordinate_space"] == "unrotated_page_points"
    assert structure["elements"][0]["bbox"] == [10.0, 120.0, 30.0, 180.0]
    assert evidence == original_evidence

    request, timeout = calls[0]
    assert request.full_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert request.method == "POST"
    assert dict(request.header_items()) == {
        "Content-type": "application/json",
        "Accept": "application/json",
    }
    assert 0 < timeout <= config["deadline_seconds"]
    request_json = json.loads(request.data)
    assert set(request_json) == {
        "model",
        "messages",
        "temperature",
        "stream",
        "max_tokens",
        "response_format",
    }
    assert request_json["model"] == config["model_id"]
    assert request_json["temperature"] == 0
    assert request_json["stream"] is False
    assert request_json["max_tokens"] == 4096
    assert request_json["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "page_structure_body",
            "strict": True,
            "schema": page_structure_generation.PAGE_STRUCTURE_BODY_SCHEMA,
        },
    }
    body_schema = page_structure_generation.PAGE_STRUCTURE_BODY_SCHEMA
    assert body_schema["additionalProperties"] is False
    nonempty_pattern = body_schema["$defs"]["nonempty_string"]["pattern"]
    assert nonempty_pattern == r"^[\s\S]*\S[\s\S]*$"
    assert re.fullmatch(nonempty_pattern, " visible ") is not None
    assert re.fullmatch(nonempty_pattern, " \t\n ") is None
    assert re.fullmatch(nonempty_pattern, "first line\nsecond line") is not None
    assert all(
        text in page_structure_generation.PAGE_STRUCTURE_PROMPT
        for text in (
            "target page",
            "Adjacent page images",
            "normalized_render_1000",
            "[x0, y0, x1, y1]",
            "do not invent content",
            "exactly once in reading_order",
            "Do not add duplicate, self, or inverse relations",
            "visible internal nodes or connections",
            "instead of guessing",
        )
    )
    assert "visible table field" not in page_structure_generation.PAGE_STRUCTURE_PROMPT
    assert set(body_schema["required"]) == {
        "elements",
        "reading_order",
        "spatial_relations",
    }
    object_schemas = [body_schema]
    pending = [body_schema]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("type") == "object" and value is not body_schema:
                object_schemas.append(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    assert all(schema["additionalProperties"] is False for schema in object_schemas)
    element_types = set()
    for schema in body_schema["properties"]["elements"]["items"]["anyOf"]:
        type_schema = schema["properties"]["type"]
        element_types.update(type_schema.get("enum", [type_schema.get("const")]))
    assert element_types == {
        "heading", "paragraph", "list", "code", "formula", "matrix", "table",
        "diagram_node", "diagram_label", "arrow", "other_visible_region",
    }
    relation_types = set()
    for schema in body_schema["properties"]["spatial_relations"]["items"]["anyOf"]:
        type_schema = schema["properties"]["type"]
        relation_types.update(type_schema.get("enum", [type_schema.get("const")]))
    assert relation_types == {"left_of", "above", "contains", "directed_arrow"}
    assert request_json["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": page_structure_generation.PAGE_STRUCTURE_PROMPT,
                },
                {
                    "type": "text",
                    "text": "Target page. Output elements from this image only.",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + __import__("base64").b64encode(render_bytes).decode("ascii")
                    },
                },
            ],
        }
    ]
    serialized_request = request.data.decode("utf-8")
    for excluded in (
        evidence["material_ref"],
        evidence["page_ref"],
        evidence["evidence_ref"],
        evidence["hashes"]["render_sha256"],
        config["runtime_id"],
    ):
        assert excluded not in serialized_request
    proxy_handler = next(
        handler
        for handler in handlers
        if isinstance(handler, page_structure_generation.urllib.request.ProxyHandler)
    )
    redirect_handler = next(
        handler
        for handler in handlers
        if isinstance(handler, page_structure_generation._NoRedirect)
    )
    assert proxy_handler.proxies == {}
    with pytest.raises(urllib.error.HTTPError) as redirect:
        redirect_handler.http_error_302(
            request, _Response(b""), 302, "redirect", {"Location": "http://example.com"}
        )
    assert redirect.value.code == 302

    cache_path = Path(config["cache_dir"]) / f"{result['cache_key']}.json"
    record = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(record) == {
        "cache_key",
        "input_evidence_ref",
        "runtime_identity",
        "page_structure",
    }
    cached = generate_page_structure(evidence, render_bytes, config)
    assert cached["reason_code"] == "PAGE_STRUCTURE_CACHE_HIT"
    assert len(calls) == 1

    record["page_structure"]["elements"][0]["bbox"] = [-1, 0, 2, 2]
    cache_path.write_text(json.dumps(record), encoding="utf-8")
    refreshed = generate_page_structure(evidence, render_bytes, config)
    assert refreshed["reason_code"] == "PAGE_STRUCTURE_READY"
    assert len(calls) == 2


def test_adjacent_pages_are_context_only_and_change_cache_key(tmp_path, monkeypatch):
    """驗證前後頁依頁碼排序送出，且有無上下文不會共用 cache。"""
    target_render = _render_bytes()
    target_evidence = _page_evidence(target_render)
    target_evidence["page_number"] = 2
    _bind_evidence(target_evidence)

    previous_render = b"\x89PNG\r\n\x1a\nprevious-page"
    previous_evidence = _page_evidence(previous_render)
    next_render = b"\x89PNG\r\n\x1a\nnext-page"
    next_evidence = _page_evidence(next_render)
    next_evidence["page_number"] = 3
    _bind_evidence(next_evidence)

    calls = []
    monkeypatch.setattr(
        page_structure_generation.urllib.request,
        "build_opener",
        _successful_build_opener(calls),
    )
    config = _config(tmp_path / "cache")
    without_context = generate_page_structure(target_evidence, target_render, config)
    with_context = generate_page_structure(
        target_evidence,
        target_render,
        config,
        nearby_pages=[
            {"page_evidence": next_evidence, "render_bytes": next_render},
            {"page_evidence": previous_evidence, "render_bytes": previous_render},
        ],
    )

    assert with_context["processing"] == "succeeded"
    assert with_context["cache_key"] != without_context["cache_key"]
    content = json.loads(calls[1][0].data)["messages"][0]["content"]
    assert [item.get("text") for item in content if item["type"] == "text"] == [
        page_structure_generation.PAGE_STRUCTURE_PROMPT,
        "Previous page context. Do not output elements from this image.",
        "Target page. Output elements from this image only.",
        "Next page context. Do not output elements from this image.",
    ]
    images = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
    assert images == [
        "data:image/png;base64,"
        + __import__("base64").b64encode(render).decode("ascii")
        for render in (previous_render, target_render, next_render)
    ]


def test_rejects_page_context_from_another_pdf(tmp_path, monkeypatch):
    """驗證不同 PDF 的相鄰頁不會被送給模型。"""
    calls = []
    monkeypatch.setattr(
        page_structure_generation.urllib.request,
        "build_opener",
        _successful_build_opener(calls),
    )
    target_render = _render_bytes()
    target_evidence = _page_evidence(target_render)
    target_evidence["page_number"] = 2
    _bind_evidence(target_evidence)
    other_render = b"\x89PNG\r\n\x1a\nother-pdf"
    other_evidence = _page_evidence(other_render)
    other_evidence["hashes"]["source_sha256"] = "f" * 64
    _bind_evidence(other_evidence)

    result = generate_page_structure(
        target_evidence,
        target_render,
        _config(tmp_path / "cache"),
        nearby_pages=[
            {"page_evidence": other_evidence, "render_bytes": other_render}
        ],
    )

    assert result["processing"] == "failed"
    assert result["reason_code"] == "PAGE_CONTEXT_INVALID"
    assert calls == []


def test_page_structure_body_schema_uses_luna_compatible_keywords():
    """驗證 union 與固定字串型別符合 Luna Structured Outputs 限制。"""
    pending = [page_structure_generation.PAGE_STRUCTURE_BODY_SCHEMA]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            assert "oneOf" not in value
            if "enum" in value or "const" in value:
                assert value["type"] == "string"
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


@pytest.mark.parametrize("node_id", [None, "node-1"], ids=["without-node", "with-node"])
def test_diagram_label_node_id_is_optional_in_schema_and_validator(
    tmp_path, monkeypatch, node_id
):
    """驗證 diagram_label 有無 node_id 都只符合一個 shape 且能通過 validator。"""
    label = {
        "id": "label-1",
        "type": "diagram_label",
        "bbox": [100, 100, 400, 300],
        "text": "Visible label",
    }
    elements = [label]
    if node_id is not None:
        label["node_id"] = node_id
        elements.insert(
            0,
            {"id": node_id, "type": "diagram_node", "bbox": [50, 50, 450, 350]},
        )

    element_schemas = page_structure_generation.PAGE_STRUCTURE_BODY_SCHEMA["properties"][
        "elements"
    ]["items"]["anyOf"]
    label_schemas = [
        schema
        for schema in element_schemas
        if schema["properties"]["type"].get("const") == "diagram_label"
    ]
    matching_shapes = [
        schema
        for schema in label_schemas
        if set(schema["required"]).issubset(label)
        and set(label).issubset(schema["properties"])
    ]
    assert len(label_schemas) == 2
    assert len(matching_shapes) == 1

    body = {
        "elements": elements,
        "reading_order": ["label-1"],
        "spatial_relations": [],
    }

    def build_opener(*_handlers):
        return _Opener(lambda _request, _timeout: _Response(_outer_response(body)))

    monkeypatch.setattr(
        page_structure_generation.urllib.request, "build_opener", build_opener
    )
    render_bytes = _render_bytes()
    result = generate_page_structure(
        _page_evidence(render_bytes), render_bytes, _config(tmp_path / "cache")
    )

    assert result["processing"] == "succeeded"
    assert result["reason_code"] == "PAGE_STRUCTURE_READY"
    output_label = next(
        element
        for element in result["page_structure"]["elements"]
        if element["type"] == "diagram_label"
    )
    assert output_label.get("node_id") == node_id


@pytest.mark.parametrize(
    "identity_part",
    [
        "source_sha256",
        "page_number",
        "evidence_ref",
        "render_schema",
        "render_sha256",
        "model_id",
        "model_revision",
        "model_artifact_sha256",
        "projector_sha256",
        "runtime_id",
        "prompt_version",
        "prompt_sha256",
        "page_structure_schema",
        "processing_policy_version",
    ],
)
def test_cache_key_invalidates(tmp_path, monkeypatch, identity_part):
    """驗證每一項 output identity 改變時都不會沿用既有 cache key。"""
    calls = []
    monkeypatch.setattr(
        page_structure_generation.urllib.request,
        "build_opener",
        _successful_build_opener(calls),
    )
    render_bytes = _render_bytes()
    evidence = _page_evidence(render_bytes)
    config = _config(tmp_path / "cache")
    baseline = generate_page_structure(evidence, render_bytes, config)
    assert baseline["processing"] == "succeeded"

    changed_evidence = deepcopy(evidence)
    changed_config = deepcopy(config)
    changed_render = render_bytes
    if identity_part == "source_sha256":
        changed_evidence["hashes"]["source_sha256"] = "f" * 64
        _bind_evidence(changed_evidence)
    elif identity_part == "page_number":
        changed_evidence["page_number"] = 2
        _bind_evidence(changed_evidence)
    elif identity_part == "evidence_ref":
        changed_evidence["hashes"]["native_sha256"] = "f" * 64
        _bind_evidence(changed_evidence)
    elif identity_part == "render_schema":
        changed_evidence["render"]["schema"] = "page-render/v2"
    elif identity_part == "render_sha256":
        changed_render += b"new"
        changed_evidence["hashes"]["render_sha256"] = hashlib.sha256(
            changed_render
        ).hexdigest()
        _bind_evidence(changed_evidence)
    elif identity_part in changed_config:
        if identity_part in {"model_artifact_sha256", "projector_sha256"}:
            changed_config[identity_part] = "f" * 64
        elif identity_part == "processing_policy_version":
            changed_config[identity_part] = "s1-page-understanding-policy/v2"
        else:
            changed_config[identity_part] += "-new"
    elif identity_part == "prompt_version":
        monkeypatch.setattr(
            page_structure_generation,
            "PAGE_STRUCTURE_PROMPT_VERSION",
            "page-structure-prompt/v5",
        )
    elif identity_part == "prompt_sha256":
        monkeypatch.setattr(
            page_structure_generation,
            "PAGE_STRUCTURE_PROMPT",
            page_structure_generation.PAGE_STRUCTURE_PROMPT + " ",
        )
    else:
        monkeypatch.setattr(
            page_structure_generation, "PAGE_STRUCTURE_SCHEMA", "page-structure/v2"
        )

    changed = generate_page_structure(changed_evidence, changed_render, changed_config)
    assert changed["cache_key"] != baseline["cache_key"]
    if identity_part == "processing_policy_version":
        assert changed["reason_code"] == "PAGE_STRUCTURE_READY"
    if identity_part == "page_structure_schema":
        changed_body_schema = deepcopy(
            page_structure_generation.PAGE_STRUCTURE_BODY_SCHEMA
        )
        changed_body_schema["properties"]["elements"]["minItems"] = 1
        monkeypatch.setattr(
            page_structure_generation,
            "PAGE_STRUCTURE_BODY_SCHEMA",
            changed_body_schema,
        )
        body_schema_changed = generate_page_structure(
            changed_evidence, changed_render, changed_config
        )
        assert body_schema_changed["cache_key"] != changed["cache_key"]


@pytest.mark.parametrize(
    ("case", "processing", "reason", "expected_calls"),
    [
        ("evidence", "failed", "PAGE_EVIDENCE_BINDING_INVALID", 0),
        ("material_binding", "failed", "PAGE_EVIDENCE_BINDING_INVALID", 0),
        ("page_binding", "failed", "PAGE_EVIDENCE_BINDING_INVALID", 0),
        ("evidence_binding", "failed", "PAGE_EVIDENCE_BINDING_INVALID", 0),
        ("native_hash", "failed", "PAGE_EVIDENCE_BINDING_INVALID", 0),
        ("hash", "failed", "RENDER_HASH_MISMATCH", 0),
        ("config", "failed", "LOCAL_CONFIG_INVALID", 0),
        ("endpoint", "failed", "LOCAL_ENDPOINT_NOT_LOOPBACK", 0),
        ("endpoint_query", "failed", "LOCAL_ENDPOINT_NOT_LOOPBACK", 0),
        ("endpoint_fragment", "failed", "LOCAL_ENDPOINT_NOT_LOOPBACK", 0),
        ("timeout", "partial", "LOCAL_PROVIDER_TIMEOUT", 2),
        ("rate_limit", "partial", "LOCAL_PROVIDER_RATE_LIMITED", 2),
        ("server_error", "partial", "LOCAL_PROVIDER_TRANSIENT_ERROR", 2),
        ("connection", "partial", "LOCAL_PROVIDER_TRANSIENT_ERROR", 2),
        ("client_error", "failed", "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", 1),
        ("redirect", "failed", "LOCAL_PROVIDER_AUTH_OR_CONFIG_ERROR", 1),
        ("truncated", "failed", "MODEL_RESPONSE_TRUNCATED", 1),
        ("outer_json", "failed", "MODEL_RESPONSE_INVALID_JSON", 1),
        ("content_json", "failed", "MODEL_RESPONSE_INVALID_JSON", 1),
        ("normalized_bbox", "failed", "PAGE_STRUCTURE_INVALID", 1),
        ("schema", "failed", "ELEMENT_SHAPE_INVALID", 1),
        ("write", "failed", "PAGE_STRUCTURE_CACHE_WRITE_FAILED", 1),
    ],
)
def test_generate_page_structure_failures(
    tmp_path, monkeypatch, case, processing, reason, expected_calls
):
    """驗證 preflight、Provider、JSON、schema 與 cache 失敗都會 fail closed。"""
    render_bytes = _render_bytes()
    evidence = _page_evidence(render_bytes)
    config = _config(tmp_path / "cache")
    calls = []

    if case == "evidence":
        evidence.pop("geometry")
    elif case == "material_binding":
        evidence["material_ref"] = f"material:sha256:{'f' * 64}"
    elif case == "page_binding":
        evidence["page_ref"] = f"page:sha256:{'f' * 64}"
    elif case == "evidence_binding":
        evidence["evidence_ref"] = f"evidence:sha256:{'f' * 64}"
    elif case == "native_hash":
        evidence["hashes"]["native_sha256"] = "F" * 64
    elif case == "hash":
        render_bytes += b"changed"
    elif case == "config":
        config["extra"] = "not-approved"
    elif case == "endpoint":
        config["endpoint_url"] = "http://example.com:8080"
    elif case == "endpoint_query":
        config["endpoint_url"] = "http://127.0.0.1:8080?"
    elif case == "endpoint_fragment":
        config["endpoint_url"] = "http://127.0.0.1:8080#"

    def urlopen(request, timeout):
        calls.append((request, timeout))
        if case == "timeout":
            raise socket.timeout("timed out")
        if case == "rate_limit":
            raise urllib.error.HTTPError(request.full_url, 429, "limited", None, None)
        if case == "server_error":
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", None, None)
        if case == "connection":
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        if case == "client_error":
            raise urllib.error.HTTPError(request.full_url, 400, "bad request", None, None)
        if case == "redirect":
            raise urllib.error.HTTPError(request.full_url, 302, "redirect", None, None)
        if case == "truncated":
            return _Response(_outer_response(finish_reason="length"))
        if case == "outer_json":
            return _Response(b"not-json")
        if case == "content_json":
            return _Response(
                json.dumps(
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": "not-json"},
                            }
                        ]
                    }
                ).encode("utf-8")
            )
        if case == "normalized_bbox":
            body = _model_body()
            body["elements"][0]["bbox"] = [0, 0, 1001, 10]
            return _Response(_outer_response(body))
        if case == "schema":
            body = _model_body()
            body["elements"][0].pop("text")
            return _Response(_outer_response(body))
        return _Response(_outer_response())

    monkeypatch.setattr(
        page_structure_generation.urllib.request,
        "build_opener",
        lambda *handlers: _Opener(urlopen),
    )
    if case == "write":
        monkeypatch.setattr(
            page_structure_generation.Path,
            "write_bytes",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("write failed")),
        )

    result = generate_page_structure(evidence, render_bytes, config)

    assert result["processing"] == processing
    assert result["reason_code"] == reason
    assert "page_structure" not in result
    assert len(calls) == expected_calls


def test_finalizer_is_pure_and_returns_validator_valid_current_artifact():
    """valid body 轉為 native bbox，且 caller inputs 完全不被改寫。"""
    evidence = _page_evidence()
    body = _model_body()
    original_evidence = deepcopy(evidence)
    original_body = deepcopy(body)

    page_structure = finalize_page_structure(body, evidence)

    assert body == original_body
    assert evidence == original_evidence
    assert validate_page_structure(page_structure, evidence) is None
    assert page_structure == {
        "schema": "page-structure/v1",
        "material_ref": evidence["material_ref"],
        "page_ref": evidence["page_ref"],
        "page_number": 1,
        "input_evidence_ref": evidence["evidence_ref"],
        "coordinate_space": "unrotated_page_points",
        "elements": [
            {
                "id": "heading-1",
                "type": "heading",
                "bbox": [10.0, 120.0, 30.0, 180.0],
                "text": "Visible heading",
            }
        ],
        "reading_order": ["heading-1"],
        "spatial_relations": [],
    }


@pytest.mark.parametrize(
    "case",
    [
        "body_type",
        "extra_field",
        "missing_field",
        "elements_type",
        "bbox_type",
        "bbox_range",
        "reading_order",
        "relation",
        "evidence_type",
        "evidence_binding",
    ],
)
def test_finalizer_rejects_malformed_body_order_relation_and_evidence(case):
    """body shape、bbox、order、relation 與 Evidence 錯誤皆 fail closed。"""
    evidence = _page_evidence()
    body = _model_body()
    if case == "body_type":
        body = []
    elif case == "extra_field":
        body["extra"] = True
    elif case == "missing_field":
        body.pop("reading_order")
    elif case == "elements_type":
        body["elements"] = {}
    elif case == "bbox_type":
        body["elements"][0]["bbox"] = "100,100,400,300"
    elif case == "bbox_range":
        body["elements"][0]["bbox"] = [0, 0, 1001, 10]
    elif case == "reading_order":
        body["reading_order"] = ["unknown-element"]
    elif case == "relation":
        body["spatial_relations"] = [
            {
                "type": "above",
                "source_id": "heading-1",
                "target_id": "unknown-element",
            }
        ]
    elif case == "evidence_type":
        evidence = []
    elif case == "evidence_binding":
        evidence["evidence_ref"] = f"evidence:sha256:{'0' * 64}"

    original_body = deepcopy(body)
    original_evidence = deepcopy(evidence)

    assert finalize_page_structure(body, evidence) is None
    assert body == original_body
    assert evidence == original_evidence

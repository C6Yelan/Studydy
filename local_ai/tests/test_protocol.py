import base64
import json

import pytest

from studydy_local_ai.protocol import (
    ProtocolError,
    decode_json_object,
    validate_ocr_request,
    validate_assessment_request,
    validate_relation_request,
)


def test_json_contract_rejects_duplicate_nan_and_deep_values():
    for encoded in (
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        (b'{"x":' * 33) + b'0' + (b'}' * 33),
    ):
        with pytest.raises(ProtocolError):
            decode_json_object(encoded, 10_000)


def test_ocr_request_decodes_only_bound_png():
    png = b"\x89PNG\r\n\x1a\npublic"
    request = {
        "schema": "local-ocr-request/v1",
        "request_id": "page-1",
        "render": {
            "sha256": "1" * 64,
            "width": 100,
            "height": 200,
            "png_base64": base64.b64encode(png).decode("ascii"),
        },
    }
    assert validate_ocr_request(request)["png_bytes"] == png
    request["render"]["extra"] = True
    with pytest.raises(ProtocolError):
        validate_ocr_request(request)


def test_relation_request_accepts_only_structural_type_and_bounded_premise():
    request = {
        "schema": "local-relation-verifier-request/v1",
        "request_id": "relation-1",
        "relation_type": "contains",
        "premise": "A: Parent\nB: Child",
    }
    assert validate_relation_request(request) == {
        "request_id": "relation-1",
        "relation_type": "contains",
        "premise": "A: Parent\nB: Child",
    }
    for invalid in (
        {**request, "relation_type": "related"},
        {**request, "premise": ""},
        {**request, "premise": "x" * 16_385},
    ):
        with pytest.raises(ProtocolError, match="CHILD_REQUEST_INVALID"):
            validate_relation_request(invalid)


def test_assessment_request_requires_exactly_four_bounded_options():
    request = {
        "schema": "local-assessment-verifier-request/v1",
        "request_id": "assessment-1",
        "premise": "Exact Evidence",
        "options": ["A", "B", "C", "D"],
    }
    assert validate_assessment_request(request) == {
        "request_id": "assessment-1",
        "premise": "Exact Evidence",
        "options": ["A", "B", "C", "D"],
    }
    for invalid in (
        {**request, "options": ["A", "B", "C"]},
        {**request, "options": ["A", "B", "C", ""]},
        {**request, "premise": ""},
        {**request, "extra": True},
    ):
        with pytest.raises(ProtocolError, match="CHILD_REQUEST_INVALID"):
            validate_assessment_request(invalid)

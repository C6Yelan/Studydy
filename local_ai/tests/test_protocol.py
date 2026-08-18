import base64
import json

import pytest

from studydy_local_ai.protocol import (
    MODEL_OUTPUT_TRUNCATED,
    ProtocolError,
    decode_json_object,
    validate_concept_request,
    validate_ocr_request,
)


def test_truncated_output_uses_fixed_sanitized_reason():
    assert MODEL_OUTPUT_TRUNCATED == "MODEL_OUTPUT_TRUNCATED"


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


def test_concept_request_binds_attempt_and_semantic_schema():
    request = {
        "schema": "local-concept-request/v1",
        "request_id": "semantic-1",
        "attempt": 2,
        "semantic_request": {"schema": "semantic-qualification-input/v1"},
    }
    assert validate_concept_request(request)["attempt"] == 2
    request["attempt"] = 3
    with pytest.raises(ProtocolError):
        validate_concept_request(request)

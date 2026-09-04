import sys

import pytest

from pdf_evidence.local_ai_process import LocalAIError, LocalAIProcess


def test_bounded_ndjson_pipe_and_clean_exit():
    code = "import sys,json; value=json.loads(sys.stdin.buffer.readline()); print(json.dumps({'request_id':value['request_id']}),flush=True)"
    child = LocalAIProcess([sys.executable, "-c", code], request_limit=100, response_limit=100)
    assert child.request({"request_id": "public"}, None) == {"request_id": "public"}
    child.close()
    child.close()


def test_timeout_aborts_only_owned_ocr_child():
    child = LocalAIProcess(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        request_limit=2_000_000,
        response_limit=100,
    )
    with pytest.raises(LocalAIError, match="CHILD_TIMEOUT"):
        child.request({"render": "x" * 1_000_000}, 0.01)
    child.close()


def test_child_failure_and_limits_do_not_expose_stderr():
    child = LocalAIProcess([sys.executable, "-c", "raise SystemExit(7)"], request_limit=10, response_limit=10)
    with pytest.raises(LocalAIError, match="CHILD_EXITED"):
        child.close()
    child.close()

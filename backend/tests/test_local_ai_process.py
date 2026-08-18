import sys

import pytest

from pdf_evidence.local_ai_process import LocalAIError, LocalAIProcess


def test_bounded_ndjson_pipe_and_clean_exit():
    code = "import sys,json; value=json.loads(sys.stdin.buffer.readline()); print(json.dumps({'request_id':value['request_id']}),flush=True)"
    child = LocalAIProcess([sys.executable, "-c", code], request_limit=100, response_limit=100)
    assert child.request({"request_id": "public"}, 2) == {"request_id": "public"}
    child.close()
    child.close()


def test_real_child_timeout_is_not_masked_and_cleanup_is_idempotent():
    child = LocalAIProcess(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        request_limit=100,
        response_limit=100,
    )
    with pytest.raises(LocalAIError, match="CHILD_TIMEOUT"):
        child.request({"request_id": "public"}, 0.01)
    child.close()
    child.close()


def test_real_nonzero_child_remains_child_exited_and_close_is_idempotent():
    child = LocalAIProcess(
        [sys.executable, "-c", "raise SystemExit(7)"],
        request_limit=100,
        response_limit=100,
    )
    with pytest.raises(LocalAIError, match="CHILD_EXITED"):
        child.close()
    child.close()


def test_child_exit_request_limit_and_stderr_are_sanitized():
    child = LocalAIProcess(
        [sys.executable, "-c", "raise SystemExit(1)"],
        request_limit=10,
        response_limit=10,
    )
    with pytest.raises(LocalAIError):
        child.request({"too_long": "public"}, 2)
    child.abort()

    code = "import sys,json; value=json.loads(sys.stdin.buffer.readline()); print(json.dumps(value),flush=True); print('diagnostic',file=sys.stderr,flush=True)"
    child = LocalAIProcess([sys.executable, "-c", code], request_limit=100, response_limit=100)
    assert child.request({"request_id": "public"}, 2) == {"request_id": "public"}
    with pytest.raises(LocalAIError, match="CHILD_RESPONSE_INVALID"):
        child.close()

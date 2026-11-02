"""舊 Luna runtime 的唯讀相容測試資料；不得用於新工作。"""

from copy import deepcopy

MODEL = "gpt-5.6-luna"
SERVER = {"package": "codex-exec", "version": "luna-test/v1", "python": "3.12"}


def test_lock(lock):
    result = deepcopy(lock)
    result["semantic_service"].update(
        model_id=MODEL,
        revision="codex-exec:luna-test/v1",
        api_protocol="codex-exec-luna/v1",
        max_model_len=65536,
        server=deepcopy(SERVER),
    )
    return result

"""本輪明確啟用的 Luna 替代測試；新工作保存真實 provider 身分。"""
from copy import deepcopy

MODEL='gpt-5.6-luna'
SERVER={'package':'codex-exec','version':'luna-test/v1','python':'3.12'}


def test_lock(lock):
    result=deepcopy(lock)
    result['semantic_service'].update(model_id=MODEL,revision='codex-exec:luna-test/v1',
        api_protocol='codex-exec-luna/v1',max_model_len=65536,server=deepcopy(SERVER))
    return result


def enabled(lock):
    return lock.get('semantic_service',{}).get('api_protocol')=='codex-exec-luna/v1'

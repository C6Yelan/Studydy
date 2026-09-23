"""教材接續共用一套設定比對；完整 lock 留作原工作稽核，assessment 不參與相容性。"""
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256


def runtime_binding_is_valid(value: Any) -> bool:
    """驗證目前 HTTP runtime binding 的形狀與內容雜湊。"""
    def digest(value):
        return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

    try:
        if not isinstance(value, dict) or set(value) != {
            "schema", "python", "runtime_lock_sha256", "model_id", "model_revision",
            "semantic_service", "ocr", "policy", "runtime_binding_sha256",
        }:
            return False
        identity = {key: item for key, item in value.items() if key != "runtime_binding_sha256"}
        if (value["schema"] != "material-runtime-binding/v1" or value["python"] != "3.12"
            or value["runtime_binding_sha256"] != canonical_sha256(identity)
            or not digest(value["runtime_lock_sha256"])
            or value["policy"] != "evidence-unified-semantics-product/v1"
            or value["ocr"] != {"model_id": "Unlimited-OCR", "revision": "07dea832e22aefee32ad281d4b80551282e1c168"}):
            return False
        service = value["semantic_service"]
        if not isinstance(service, dict):
            return False
        return (all(isinstance(value[k], str) and value[k].strip() for k in ("model_id", "model_revision"))
                and service == {
                    "base_url": "http://127.0.0.1:18000", "max_model_len": 32768,
                    "server": {"package": "vllm", "version": "0.28.0", "python": "3.12",
                               "torch": "2.13.0+cu130", "cuda": "13.0", "transformers": "5.15.1"},
                })
    except (KeyError, TypeError, ValueError):
        return False


def lock_matches_binding(lock, binding):
    if not isinstance(lock, dict) or not runtime_binding_is_valid(binding):
        return False
    try:
        if binding['python'] != lock['python'] or binding['ocr'] != {k: lock['ocr'][k] for k in ('model_id', 'revision')}:
            return False
        service = binding['semantic_service']
        saved_service = lock['semantic_service']
        return (lock.get('schema') == 'studydy-runtime-lock/v1'
                and service == {k: saved_service[k] for k in ('base_url', 'max_model_len', 'server')}
                and binding['model_id'] == saved_service['model_id']
                and binding['model_revision'] == saved_service['revision']
                and binding['runtime_lock_sha256'] == canonical_sha256(lock))
    except (KeyError, TypeError, ValueError):
        return False


def same_material_runtime(first_lock, first_binding, second_lock, second_binding):
    if not lock_matches_binding(first_lock, first_binding) or not lock_matches_binding(second_lock, second_binding):
        return False
    fields = ('python', 'packages', 'ocr', 'semantic_service', 'material_semantics')
    if any(key not in first_lock or key not in second_lock or first_lock[key] != second_lock[key] for key in fields):
        return False
    if first_lock.get('material_review') != second_lock.get('material_review'):
        return False
    return True

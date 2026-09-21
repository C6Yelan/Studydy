"""教材接續共用一套設定比對；完整 lock 留作原工作稽核，assessment 不參與相容性。"""
from pdf_evidence.ocr_page_evidence import canonical_sha256
from .storage.knowledge_structures import runtime_binding_is_valid


def lock_matches_binding(lock, binding):
    if not isinstance(lock, dict) or not runtime_binding_is_valid(binding):
        return False
    service = binding['semantic_service']
    if service.get('transport') == 'command':
        execution = {key: value for key, value in service.items() if key != 'runtime_lock_sha256'}
        digest = canonical_sha256({'base_runtime_lock': lock, 'execution': execution})
    else:
        digest = canonical_sha256(lock)
    return digest == binding['runtime_lock_sha256']


def same_material_runtime(first_lock, first_binding, second_lock, second_binding):
    if not lock_matches_binding(first_lock, first_binding) or not lock_matches_binding(second_lock, second_binding):
        return False
    fields = ('python', 'packages', 'ocr', 'semantic_service', 'material_semantics')
    if any(key not in first_lock or key not in second_lock or first_lock[key] != second_lock[key] for key in fields):
        return False
    if first_lock.get('material_review') != second_lock.get('material_review'):
        return False
    # 實際執行模型與 command 設定仍是教材依賴。
    first_service = {key: value for key, value in first_binding['semantic_service'].items() if key != 'runtime_lock_sha256'}
    second_service = {key: value for key, value in second_binding['semantic_service'].items() if key != 'runtime_lock_sha256'}
    return first_service == second_service

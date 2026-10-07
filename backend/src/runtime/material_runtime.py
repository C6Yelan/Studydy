"""比對教材接續所需的分析設定，評量設定不影響教材接續。"""
from copy import deepcopy
import importlib.metadata
import os
from pathlib import Path
import stat
from typing import Any

from pdf_evidence.ocr_page_evidence import canonical_sha256
from pdf_evidence.material_pipeline import MaterialAnalysisError, validate_runtime_lock
from .semantic_service import SemanticServiceError, preflight_semantic_service


_CONFIG_KEYS = {"private_runtime_root", "runtime_lock", "python_executable", "site_packages", "ocr_model_root"}
_RUNTIME_COMPONENTS = {"layout", "runtime_lock", "python_runtime", "ocr_package", "ocr_model", "semantic_service"}
_RUNTIME_REASONS = {
    "LOCAL_RUNTIME_MISSING", "LOCAL_RUNTIME_UNSAFE_TARGET", "LOCAL_RUNTIME_NOT_EXECUTABLE",
    "LOCAL_RUNTIME_VERSION_MISMATCH", "LOCAL_RUNTIME_SMOKE_FAILED",
    "LOCAL_RUNTIME_SETTINGS_MISMATCH", "LOCAL_RUNTIME_LOCK_MISMATCH", "LOCAL_RUNTIME_WRITE_FAILED",
}


class MaterialRuntimeError(RuntimeError):
    """設定／環境錯誤只攜帶固定公開原因碼，不依賴工作執行模組。"""

    def __init__(self, component: str, reason: str) -> None:
        super().__init__("MATERIAL_CONFIGURATION_INVALID")
        self.component = component if component in _RUNTIME_COMPONENTS else None
        self.reason = reason if reason in _RUNTIME_REASONS else None


def _existing_path(value: str, *, directory: bool, component: str) -> Path:
    try:
        path = Path(value)
        mode = path.stat().st_mode
    except (OSError, TypeError):
        raise MaterialRuntimeError(component, "LOCAL_RUNTIME_MISSING") from None
    if not path.is_absolute() or (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)) is False:
        raise MaterialRuntimeError(component, "LOCAL_RUNTIME_UNSAFE_TARGET")
    return path


def runtime_binding(local_config: Any) -> dict[str, Any]:
    if not isinstance(local_config, dict) or set(local_config) != _CONFIG_KEYS:
        raise MaterialRuntimeError("layout", "LOCAL_RUNTIME_SETTINGS_MISMATCH")
    try:
        root = Path(local_config["private_runtime_root"])
        site_packages = Path(local_config["site_packages"])
        install_root = site_packages.parents[4]
        expected = {
            "private_runtime_root": install_root / "runtime",
            "python_executable": install_root / "ocr/runtime/bin/python3.12",
            "site_packages": install_root / "ocr/runtime/lib/python3.12/site-packages",
            "ocr_model_root": install_root / "models/unlimited-ocr",
        }
        if root.is_symlink() or any(Path(local_config[key]) != path for key, path in expected.items()):
            raise ValueError
        lock = validate_runtime_lock(local_config["runtime_lock"], assessment=False)
    except (IndexError, KeyError, MaterialAnalysisError, TypeError, ValueError):
        raise MaterialRuntimeError("runtime_lock", "LOCAL_RUNTIME_LOCK_MISMATCH") from None
    binding = {
        "schema": "material-runtime-binding/v1",
        "python": lock["python"],
        "runtime_lock_sha256": canonical_sha256(lock),
        "model_id": lock["semantic_service"]["model_id"],
        "model_revision": lock["semantic_service"]["revision"],
        "semantic_service": {
            "base_url": lock["semantic_service"]["base_url"],
            "max_model_len": lock["semantic_service"]["max_model_len"],
            "server": deepcopy(lock["semantic_service"]["server"]),
        },
        "ocr": {"model_id": lock["ocr"]["model_id"], "revision": lock["ocr"]["revision"]},
        "policy": "evidence-unified-semantics-product/v1",
    }
    binding["runtime_binding_sha256"] = canonical_sha256(binding)
    return binding


def validate_installed_local_runtime(local_config: Any) -> dict[str, Any]:
    binding = runtime_binding(local_config)
    assert isinstance(local_config, dict)
    executable = _existing_path(local_config["python_executable"], directory=False, component="python_runtime")
    if not os.access(executable, os.X_OK):
        raise MaterialRuntimeError("python_runtime", "LOCAL_RUNTIME_NOT_EXECUTABLE")
    site_packages = _existing_path(local_config["site_packages"], directory=True, component="ocr_package")
    model_root = _existing_path(local_config["ocr_model_root"], directory=True, component="ocr_model")
    _existing_path(str(model_root / "config.json"), directory=False, component="ocr_model")
    lock = local_config["runtime_lock"]
    expected = lock["packages"]
    try:
        distributions = importlib.metadata.distributions(path=[str(site_packages)])
        installed = {
            distribution.metadata["Name"].lower().replace("_", "-"): distribution.version
            for distribution in distributions
            if distribution.metadata.get("Name")
        }
    except Exception:
        raise MaterialRuntimeError("ocr_package", "LOCAL_RUNTIME_VERSION_MISMATCH") from None
    if any(installed.get(name.replace("_", "-")) != version for name, version in expected.items() if name != "backend"):
        raise MaterialRuntimeError("ocr_package", "LOCAL_RUNTIME_VERSION_MISMATCH")
    return binding


def _prepare_runtime_root(value: str) -> None:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise MaterialRuntimeError("layout", "LOCAL_RUNTIME_UNSAFE_TARGET")
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            raise OSError
    except OSError:
        raise MaterialRuntimeError("layout", "LOCAL_RUNTIME_WRITE_FAILED") from None


def runtime_preflight(local_config: Any) -> dict[str, Any]:
    binding = validate_installed_local_runtime(local_config)
    assert isinstance(local_config, dict)
    try:
        preflight_semantic_service(local_config["runtime_lock"])
    except SemanticServiceError as error:
        reason = "LOCAL_RUNTIME_SETTINGS_MISMATCH" if error.reason_code.endswith(("CONFIG_INVALID", "IDENTITY_MISMATCH")) else "LOCAL_RUNTIME_MISSING"
        raise MaterialRuntimeError("semantic_service", reason) from None
    _prepare_runtime_root(local_config["private_runtime_root"])
    return binding


def runtime_binding_is_valid(value: Any) -> bool:
    """驗證模型 runtime binding 的格式與內容雜湊。"""
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
        luna = value['model_id']=='gpt-5.6-luna' and value['model_revision']=='codex-exec:luna-test/v1'
        if service.get('max_model_len') not in ((32768,65536,272000) if luna else (32768,65536)):
            return False
        return (all(isinstance(value[k], str) and value[k].strip() for k in ("model_id", "model_revision"))
                and service == {
                    "base_url": "http://127.0.0.1:18000", "max_model_len": service['max_model_len'],
                    "server": ({"package":"codex-exec", "version":"luna-test/v1", "python":"3.12"} if value["model_revision"] == "codex-exec:luna-test/v1" and value["model_id"] == "gpt-5.6-luna" else {"package": "vllm", "version": "0.28.0", "python": "3.12",
                               "torch": "2.13.0+cu130", "cuda": "13.0", "transformers": "5.15.1"}),
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
    fields = ('python', 'packages', 'ocr')
    if any(key not in first_lock or key not in second_lock or first_lock[key] != second_lock[key] for key in fields):
        return False
    first_service, second_service = first_lock['semantic_service'], second_lock['semantic_service']
    if first_service != second_service:
        # Luna 舊 272K 工作可沿用已驗證批次；後續請求仍由後端強制檢查 64K。
        # 其餘同模型、同 provider 僅擴大容量時可接續，舊 hash 保持原值。
        if not (first_service.get('api_protocol') == second_service.get('api_protocol')
                and first_service.get('api_protocol') in {'codex-exec-luna/v1','openai-chat-completions/v1'}
                and (first_service['max_model_len'] <= second_service['max_model_len']
                     or (first_service['api_protocol'] == 'codex-exec-luna/v1'
                         and first_service['max_model_len'] == 272000
                         and second_service['max_model_len'] == 65536))
                and {k:v for k,v in first_service.items() if k!='max_model_len'} ==
                    {k:v for k,v in second_service.items() if k!='max_model_len'}):
            return False
    # 額度調整不作廢已驗證的批次；各工作仍保留原始 lock 與 hash。
    for task in ('material_semantics', 'material_review'):
        first, second = first_lock.get(task), second_lock.get(task)
        if first is None and second is None:
            continue
        if not isinstance(first, dict) or not isinstance(second, dict):
            return False
        if {k: v for k, v in first.items() if k != 'max_tokens'} != {
            k: v for k, v in second.items() if k != 'max_tokens'
        }:
            return False
    return True

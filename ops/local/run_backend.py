"""從本機私密設定啟動正式 backend；不複製或輸出憑證。"""

import json
import os
from pathlib import Path


repo = Path(__file__).resolve().parents[2]
state = repo.parent / ".studydy-product"
config = json.loads((state / "private-config.json").read_text())
environment = os.environ.copy()
environment.update({
    "PYTHONPATH": str(repo / "backend/src"),
    "STUDYDY_PROFILE": "local",
    "STUDYDY_PUBLIC_ORIGIN": "http://127.0.0.1:4173",
    "STUDYDY_SECURE_COOKIE": "false",
    "STUDYDY_DATABASE_DSN": config["database_dsn"],
    "STUDYDY_ARTIFACT_ROOT": config["artifact_root"],
    "STUDYDY_LOCAL_RUNTIME_ROOT": str(Path.home() / ".local/share/studydy"),
    "STUDYDY_SEMANTIC_BASE_URL": "http://127.0.0.1:18000",
})
# 可選開發工具只由正式版 private config 注入；不固定執行器或模型。
for key, variable in (("normalizer_python", "STUDYDY_NORMALIZER_PYTHON"),
                      ("semantic_command_config", "STUDYDY_SEMANTIC_COMMAND_CONFIG")):
    if key in config:
        if not isinstance(config[key], str) or not Path(config[key]).is_absolute():
            raise ValueError("LOCAL_DEVELOPMENT_CONFIG_INVALID")
        environment[variable] = config[key]
# 模型通道在 Pod 端讀取 server key，本機不保存模型憑證。
environment.pop("STUDYDY_SEMANTIC_API_KEY", None)
python = str(repo / "backend/.venv/bin/python")
os.chdir(repo)
os.execve(python, [python, "-m", "runtime.local_app"], environment)

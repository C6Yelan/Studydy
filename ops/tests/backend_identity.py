"""核對後端映像的執行 UID 有 passwd 身分，OCR 子程序可匯入 torch／transformers。

以正式 backend 相同的 UID、唯讀 rootfs 與 /tmp tmpfs 執行，子程序環境比照
local_ai_process 只帶 HF 設定；不 build／pull、不掛載產品資料、不載入模型或 GPU。
"""

import os
import subprocess
import sys

image = sys.argv[1] if len(sys.argv) > 1 else "studydy-backend-ocr:latest"
uid = os.environ.get("STUDYDY_BACKEND_UID", "20001")
probe = """
import getpass, os, pwd
entry = pwd.getpwuid(os.getuid())
assert entry.pw_dir == "/tmp", entry.pw_dir
assert os.path.expanduser("~") == "/tmp"
getpass.getuser()
from transformers import AutoTokenizer
from torch._inductor.runtime.cache_dir_utils import cache_dir
assert cache_dir().startswith("/tmp/"), cache_dir()
import matplotlib
assert matplotlib.get_configdir().startswith("/tmp/"), matplotlib.get_configdir()
print("PASS", entry.pw_name, os.getuid())
"""
result = subprocess.run(
    [
        "docker", "run", "--rm", "--pull", "never", "--network", "none",
        "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
        "--user", f"{uid}:1000",
        "--entrypoint", "env", image,
        "-i", "HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "HF_HOME=/tmp/huggingface",
        "/opt/studydy/ocr/runtime/bin/python", "-I", "-c", probe,
    ],
    capture_output=True,
    text=True,
)
print(result.stdout.strip() or result.stderr.strip()[-2000:])
raise SystemExit(result.returncode)

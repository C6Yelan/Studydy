# 安裝與啟動

[文件入口](../README.md) · [測試](testing.md) · [限制](limitations.md)

本頁說明 Linux 本機部署。所有命令從 repository root 執行，除非另有註明。初始化步驟只適用於新環境；已有資料庫或設定時，沿用原位置，不覆寫設定、不重建 volume。

## 1. 準備依賴

| 項目 | 需求與設定來源 |
| --- | --- |
| Python | 3.12；[backend/pyproject.toml](../backend/pyproject.toml) 與 [backend/uv.lock](../backend/uv.lock) |
| Python 套件管理 | uv |
| 前端 | 支援目前 Vite 的 Node.js；開發驗證使用 Node 24，套件由 [package-lock.json](../frontend/package-lock.json) 固定 |
| 資料庫 | PostgreSQL 18；本機管理腳本使用 Docker 持久 volume |
| 文件轉檔 | LibreOffice Writer／Impress、bubblewrap、fontconfig、Noto CJK；版本與政策見 [converter.py](../backend/src/document_normalization/converter.py) |
| 模型環境 | Unlimited-OCR 與 HTTP 語意服務，見本頁第 4 節 |

轉檔器依賴 Linux namespaces，以及 /usr、/etc/fonts、/etc/libreoffice、/etc/ld.so.cache 和 /usr/lib/libreoffice/program/soffice 的主機布局。作業系統套件須依實際發行版安裝，並核對 converter 中的版本要求。

新環境安裝應用程式依賴：

~~~bash
uv sync --project backend --locked --extra test
npm --prefix frontend ci
~~~

若既有 backend/.venv 與其他 checkout 共用，採增量安裝，不以同步清除其他 checkout 的套件：

~~~bash
uv export --project backend --locked --extra test --no-dev --no-emit-project | \
  uv pip install --python backend/.venv/bin/python -r -
~~~

## 2. 建立持久資料庫與檔案目錄

管理腳本固定從 repo 的上一層讀取 .studydy-product。下列範例只用於該目錄尚不存在的新環境：

~~~bash
umask 077
mkdir -m 700 ../.studydy-product
mkdir -m 700 ../.studydy-product/artifacts
~~~

自行建立 ../.studydy-product/postgres.env，填入新資料庫密碼，檔案權限設為 0600：

~~~dotenv
POSTGRES_DB=studydy
POSTGRES_USER=studydy_owner
POSTGRES_PASSWORD=<自行設定的密碼>
PGDATA=/var/lib/postgresql/18/docker
~~~

建立只對本機開放的持久容器；不要加 --rm 或把資料目錄改為 tmpfs：

~~~bash
docker run --detach --name studydy-postgres \
  --publish 127.0.0.1:55432:5432 \
  --mount type=volume,source=studydy-pg18,target=/var/lib/postgresql \
  --env-file ../.studydy-product/postgres.env \
  postgres:18.4-bookworm
docker exec studydy-postgres pg_isready -U studydy_owner -d studydy
~~~

建立 ../.studydy-product/private-config.json。以下是欄位範例，所有佔位值都必須替換；密碼在 URI 中須 URL encode：

~~~json
{
  "container": "studydy-postgres",
  "volume": "studydy-pg18",
  "database_dsn": "postgresql://studydy_owner:<URL_ENCODED_PASSWORD>@127.0.0.1:55432/studydy",
  "artifact_root": "/absolute/path/to/.studydy-product/artifacts"
}
~~~

artifact_root 必須是絕對路徑，目錄權限為 0700。設定檔含憑證，不加入 Git：

~~~bash
chmod 600 ../.studydy-product/postgres.env ../.studydy-product/private-config.json
~~~

## 3. 初始化 schema

確認資料庫已就緒後，對剛建立的資料庫執行 migration。此命令只印套用的版本，不印 DSN：

~~~bash
PYTHONPATH=backend/src backend/.venv/bin/python - <<'PY'
import json
from pathlib import Path
from runtime.storage.migrations import run_migrations

config = json.loads(Path("../.studydy-product/private-config.json").read_text())
print(run_migrations(config["database_dsn"]))
PY
~~~

Migration runner 核對連續版本與 checksum，逐份交易套用，重跑已套用版本為 no-op。對已有資料的資料庫進行變更前，先備份 DB 與 artifact store；不可修改帳本 checksum 來略過不一致。

## 4. 準備 OCR 與語意服務

應用程式依賴安裝不包含模型權重、CUDA 或 vLLM。這些需由部署者另外準備，版本與模型 revision 以 [runtime-lock.json](../local_ai/runtime-lock.json) 為準；repo 目前沒有完整的模型安裝腳本。

本機 OCR 使用以下布局：

~~~text
~/.local/share/studydy/
  ocr/runtime/bin/python3.12
  ocr/runtime/lib/python3.12/site-packages/
  models/unlimited-ocr/
~~~

OCR runtime 須包含 lock 的 packages（含本 repo 的 studydy-local-ai 套件），模型目錄須是對應 revision 的完整權重與設定。GPU／CUDA 能力須符合模型 runtime。即使教材可走原生文字，正式分析的 preflight 仍會檢查 OCR 安裝布局。

語意服務須符合 lock 的 semantic_service：模型、revision、vLLM 套件契約、context、concurrency，以及 health、version、model discovery、tokenize、chat completions 路由。部署者負責啟動服務與核對實際權重 revision。

目前本機管理器會啟動 [SSH 模型通道](../ops/local/model_bridge.py)，對後端提供 127.0.0.1:18000。此通道有明確部署假設：

- 私密檔 ../.studydy-product/pod-connection.json 提供 ssh_host，可使用 SSH config alias。
- 使用 ~/.ssh/id_ed25519、BatchMode 與 StrictHostKeyChecking；須先完成金鑰及 known_hosts 設定。
- 遠端提供 Python 3、互動式 root shell，提示字元符合 root@…#。
- 遠端模型服務位於 127.0.0.1:18000；通道的遠端程序需能取得 VLLM_API_KEY。

~~~json
{
  "ssh_host": "studydy-model"
}
~~~

將該檔設為 0600。主機、SSH port 與使用者可在 ~/.ssh/config 的 studydy-model alias 中設定；不要把真實連線資訊寫入 repo。此通道不是通用 Pod 部署工具，也不負責安裝或啟停模型。

設定檔存在但模型離線時，本機登入與已保存內容讀取仍可使用；分析與出題會回報不可用。

## 5. 啟動與停止

~~~bash
python3 ops/local/manage.py status
python3 ops/local/manage.py start
~~~

| 服務 | 本機位置 |
| --- | --- |
| 前端 | http://127.0.0.1:4173 |
| 後端 | http://127.0.0.1:8001 |
| API 定義 | http://127.0.0.1:8001/v1/openapi.json |
| 模型通道 | http://127.0.0.1:18000 |

start 會啟動已配置的持久容器、API／worker、模型通道並建置前端。status 不呼叫模型。埠被不明程序占用時會拒絕重複啟動。

~~~bash
python3 ops/local/manage.py stop
~~~

stop 停止本機前後端與模型通道，保留 PostgreSQL、volume、教材及遠端模型。它不代表遠端 GPU 已停機。

## 排錯與日常資料

Logs 與 PID 位於 ../.studydy-product/logs、run；原檔與分析產物位於設定的 artifact_root。備份應包含 DB、artifact store 及私密設定，並在停止產品寫入的情況下保持一致。

| 問題 | 檢查方式 |
| --- | --- |
| LOCAL_ENVIRONMENT_OPERATION_FAILED | 私密設定欄位、檔案權限與設定的 Docker container 是否存在 |
| Port occupied／still starting | 先查 status 與對應 log，不反覆送出 start |
| NORMALIZER_UNAVAILABLE | 系統轉檔工具、路徑與後端 Python 依賴 |
| NORMALIZER_VERSION_MISMATCH | converter policy 與實際套件／LibreOffice 版本 |
| AI 操作不可用 | OCR 安裝布局、SSH 認證與遠端模型契約；已保存內容不需要重新分析 |

需要檢驗真實模型環境時，可明確執行：

~~~bash
PYTHONPATH=backend/src backend/.venv/bin/python -m runtime.local_runtime verify
~~~

此命令會連線語意服務並載入、關閉一次 OCR 模型，不屬於一般離線測試。服務驗證成功不代表生成品質通過，品質邊界見 [限制](limitations.md)。

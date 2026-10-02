# 安裝與啟動

[文件入口](../README.md) · [測試](testing.md) · [限制](limitations.md)

Docker 映像提供應用、Python、Node、LibreOffice、字型與 Bubblewrap；命令在本 repository 根目錄執行。

## 主機需求

- Linux 容器、Docker Engine 28.3 以上與 Docker Compose；適用環境為 x86_64 Linux／WSL2。
- Kernel 允許 unprivileged user namespaces，資料檔案系統支援 Linux owner 與 mode。
- NVIDIA GPU、驅動及 CDI 容器整合，供本機 OCR 使用。Compose 使用 nvidia.com/gpu=all。

GPU 設定依 [NVIDIA CDI 指引](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html) 與 [Docker CDI 文件](https://docs.docker.com/reference/cli/dockerd/#configure-cdi-devices) 核對。WSL2 使用 Windows GPU 驅動，不另裝 Linux GPU 驅動。

後端使用 [seccomp 規則](../ops/docker/bubblewrap-seccomp.json) 建立沙箱；能力不足時拒絕啟動，不靜默關閉隔離。

## 設定

~~~bash
cp .env.example .env
chmod 600 .env
~~~

填妥 POSTGRES_PASSWORD 與模型服務位址；.env、私鑰及持久資料不得提交 Git。

| 變數 | 用途 |
| --- | --- |
| COMPOSE_PROJECT_NAME | 部署識別，同機多份安裝須唯一 |
| STUDYDY_PORT／STUDYDY_PUBLIC_ORIGIN | 對外 port 與完整 origin，預設 4173／http://127.0.0.1:4173 |
| STUDYDY_SECURE_COOKIE | 本機 HTTP 用 false，HTTPS 用 true |
| STUDYDY_DATA_DIR | 持久資料根目錄，預設 ./data |
| STUDYDY_UID／STUDYDY_GID | 資料 owner，預設 1000 |
| POSTGRES_DB／POSTGRES_USER／POSTGRES_PASSWORD | 本部署資料庫與認證 |
| STUDYDY_SEMANTIC_BASE_URL | 模型 origin，不含 /v1、帳密、query 或 fragment |
| VLLM_API_KEY | 選用模型 Bearer token；空值不送 Authorization |
| COMPOSE_PROFILES | 直接 HTTP／HTTPS 留空；使用 SSH 設 ssh |

容器的 127.0.0.1 指向自己。模型在主機時可使用 host.docker.internal，但服務須監聽容器可達介面；其他容器或遠端服務使用其可達 origin。

## 建置與服務管理

已上線的 studydy.net 請依 [canonical 部署流程](deployment.md)從本 checkout rebuild／redeploy，
保留 `compose.yaml`＋`compose.tunnel.yaml`。以下 base-only 命令供初次本機安裝使用。

~~~bash
docker compose build
docker compose run --rm download-ocr
docker compose up -d --wait
docker compose ps
~~~

download-ocr 下載 runtime lock 指定的 Unlimited-OCR snapshot，不載入模型或覆寫未知目錄。權重保存於 data/models/unlimited-ocr，供後端唯讀掛載；已有核對過的相同 snapshot 可直接沿用。

網站：http://127.0.0.1:4173。Nginx 代理同源 /v1 API；[OpenAPI](http://127.0.0.1:4173/v1/openapi.json)提供實際契約。模型、套件與請求設定以 [runtime-lock.json](../local_ai/runtime-lock.json) 為準。

init 建立資料目錄及權限；後端依序核對 migration checksum、套用待執行版本，再啟動 API 與 worker。重跑不重建已有帳號或清空資料。登入、已保存資料讀取、上傳與轉檔不需語意模型。

~~~bash
docker compose logs --tail 100 backend
docker compose down
~~~

down 不刪除持久資料。公開服務需另外配置 TLS、正確 origin 與 Secure cookie。

## 選用 SSH 連線

直接連線不需啟用此服務。使用 SSH 時設定：

~~~dotenv
COMPOSE_PROFILES=ssh
STUDYDY_SEMANTIC_BASE_URL=http://model-bridge:18000
STUDYDY_SSH_HOST=user@host
STUDYDY_SSH_PORT=22
STUDYDY_SSH_MODEL_PORT=18000
~~~

在 STUDYDY_DATA_DIR/ssh 放置 model_key 與已核對 fingerprint 的 known_hosts，權限 0600、owner 與 STUDYDY_UID 相符；也可用 STUDYDY_SSH_KEY_FILE／STUDYDY_SSH_KNOWN_HOSTS_FILE 指定現有檔案的絕對路徑。容器只唯讀掛載這兩份檔案，不自動接受未知主機。

遠端須提供互動式 POSIX shell 與 Python 3。通道只轉送固定模型路由，從遠端 VLLM_API_KEY 取得 Bearer token；健康檢查不呼叫模型，結果不明的請求不自動重播。

切換連線方式前先用原設定停止服務，再修改.env 並啟動。連線覆寫不改已保存的模型快照、binding 或 hash。

## 資料、備份與還原

STUDYDY_DATA_DIR 包含 artifacts／postgres 及 models/unlimited-ocr。.env 與 SSH 認證須另行安全保存。

1. 備份前確認沒有進行中的工作並停止產品寫入；以 pg_dump 備份 PostgreSQL，同步保存 artifact store、私有設定、檔案權限及程式／映像版本。不要複製正在運行的 PGDATA 代替資料庫備份。
2. 還原至明確命名的空白產品還原目標，使用相符的 PostgreSQL 與程式版本。以 pg_restore --no-owner --no-acl --exit-on-error --single-transaction 還原 dump，artifact 解到獨立私人目錄。
3. 核對資料、檔案 hash、權限及 migration 帳本後再切換。保留原資料直到確認還原完成；不要覆蓋現行 DB、刪除帳本或改 checksum，也不要用測試 DB 取代產品資料。

## 驗證與排錯

不需模型的驗證見 [測試](testing.md)。模型能力檢查會實際載入 OCR 並連線服務，只在已授權時執行：

~~~bash
docker compose exec backend /app/backend/.venv/bin/python -m runtime.local_runtime verify
~~~

啟動失敗先看 backend／init logs、資料權限、資料庫與沙箱；模型操作失敗則核對服務位址、runtime lock 及已啟用的 SSH 設定。不要輸出展開後含秘密的 Compose config，或把健康檢查成功當成模型品質通過。

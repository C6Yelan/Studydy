# 安裝與啟動

[文件入口](../README.md) · [測試](testing.md) · [限制](limitations.md)

部署使用 Docker Compose，路徑以這份 repository 為基準，目錄不必叫 main。應用、Python、Node、LibreOffice、字型與 OCR 套件由映像提供；主機不需要另外安裝這些應用套件。

## 主機需求

- Linux 容器環境、Docker Engine 28.3 以上與支援 CDI devices 的 Docker Compose；目前驗證平台為 x86_64 Linux／WSL2。
- 部署預設需要 NVIDIA GPU、主機驅動及可用的容器 GPU 整合。WSL2 也需要正確配置 GPU 接入；僅在 WSL 中可用 nvidia-smi 不代表容器已能使用 GPU。無 GPU 時可先執行獨立的 [容器測試](testing.md)。
- Kernel／主機安全政策必須允許容器內的 unprivileged user namespaces。後端使用 [bubblewrap seccomp 規則](../ops/docker/bubblewrap-seccomp.json)，不使用 privileged、host PID 或 Docker socket。
- data 所在檔案系統須支援 Linux owner 與 mode；模型及 DB 需要足夠磁碟空間。

後端使用明確的 CDI 裝置 nvidia.com/gpu=all。依 [NVIDIA CDI 文件](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html) 安裝 nvidia-container-toolkit-base 並產生 CDI 設定，確認 nvidia-ctk cdi list 能列出該裝置；Docker Engine 的 [CDI 支援](https://docs.docker.com/reference/cli/dockerd/#configure-cdi-devices) 自 28.3 起預設開啟。這屬主機配置，不由 Compose 自動安裝或修改。WSL2 使用 Windows 提供的 GPU 驅動，不另安裝 Linux GPU 驅動。

Compose 會在不支援 sandbox 時拒絕啟動後端，不會靜默關閉轉檔隔離。AppArmor／SELinux 等主機政策亦可能阻擋 namespaces，須由部署者按環境核對。

## 1. 建立設定

~~~bash
cp .env.example .env
chmod 600 .env
~~~

編輯 .env，至少填妥 POSTGRES_PASSWORD 與實際模型服務位址。不要提交 .env 或 data：

| 設定 | 用途 |
| --- | --- |
| COMPOSE_PROJECT_NAME | 同一主機上的部署識別；第二份安裝須用不同名稱 |
| COMPOSE_PROFILES | 預設留空；需要 SSH 通道時設為 ssh，並依下方說明設定模型位址 |
| STUDYDY_PORT | 對外前端 port，預設 4173 |
| STUDYDY_PUBLIC_ORIGIN | 瀏覽器的完整 origin，須與 port／網址一致 |
| STUDYDY_SECURE_COOKIE | 本機 HTTP 用 false；經 HTTPS 公開時用 true |
| STUDYDY_DATA_DIR | 預設 ./data；測試必須指定自己的資料目錄 |
| STUDYDY_UID／STUDYDY_GID | 後端及教材檔案 owner；預設 1000，可用 id -u／id -g 核對 |
| POSTGRES_DB／POSTGRES_USER／POSTGRES_PASSWORD | 本部署的 PostgreSQL 設定 |
| STUDYDY_SEMANTIC_BASE_URL | 模型服務 origin，只含協定、host 與可選 port，不加 /v1 |
| VLLM_API_KEY | 選用的模型 Bearer token；空值不送 Authorization |

模型位址可用 HTTP 或 HTTPS，不能把帳密塞進 URL。外部服務宜使用 HTTPS。前端不會收到模型憑證。

## 2. 建置與準備 OCR 權重

後端映像封裝 runtime lock 指定的 PyTorch、CUDA 使用者層、Transformers 與 OCR adapter。主機 GPU 驅動不包含在映像中。

~~~bash
docker compose build
docker compose run --rm download-ocr
~~~

download-ocr 從 [baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR) 下載 lock 指定的 revision，不載入模型。它只寫 data/models/unlimited-ocr，允許續傳自己標記的相同 revision，不覆寫未知的既有模型目錄。此命令不要求 GPU 裝置，但需要網路與模型磁碟空間；日常啟動不會自動執行下載。

已有核對過的相同 snapshot 時，可直接把完整權重放在該模型目錄，無須重新下載。後端以唯讀方式掛載權重，依賴均使用容器內 OCR 環境。

## 3. 啟動與停止

~~~bash
docker compose up -d --wait
docker compose ps
~~~

預設入口是 http://127.0.0.1:4173。只有前端對主機開 port；Nginx 把 /v1 轉送到後端。API 定義可從 http://127.0.0.1:4173/v1/openapi.json 讀取。

Compose 的 init 只建立資料目錄及必要權限，不清空已有內容。PostgreSQL 就緒後，後端用原 migration runner 核對／套用 schema，再啟動 API 與 worker。重跑不重建已有帳號、教材或 schema。

帳號、資料讀取、教材上傳與轉檔可獨立使用；AI 分析／出題需要 OCR 權重與相符的語意服務，不提供模型 fallback。日常管理使用同一份部署設定：

~~~bash
docker compose logs --tail 100 backend
docker compose down
~~~

down 不刪除 bind-mounted data。不要把刪除 data 當成重新啟動方式。

## 4. 連接語意模型

RunPod 是選用供應商。Gemma 可以放在另一個容器、自有伺服器或其他 GPU 平台，只要符合 [runtime-lock.json](../local_ai/runtime-lock.json) 的模型、revision、server、tokenizer 與生成契約。

STUDYDY_SEMANTIC_BASE_URL 是容器實際連線位址。容器內的 127.0.0.1 指向容器自身，不能用它代指主機或另一個服務。host.docker.internal 會指向主機 gateway，但主機服務仍需監聽容器可達的介面；只綁主機 loopback 的服務不能直接靠改名稱存取。

直接連線 HTTP／HTTPS 服務時，COMPOSE_PROFILES 留空。若需 SSH，在 .env 設定：

~~~dotenv
COMPOSE_PROFILES=ssh
STUDYDY_SEMANTIC_BASE_URL=http://model-bridge:18000
STUDYDY_SSH_HOST=user@host
STUDYDY_SSH_PORT=22
STUDYDY_SSH_MODEL_PORT=18000
~~~

這會啟用 model-bridge，並讓後端透過該通道存取模型。SSH 主機設定在通道啟動時檢查；未啟用 SSH 的部署不需填寫。COMPOSE_PROFILES 同時用於日常 up／down，保持 .env 設定一致即可。

切換 SSH 與直接連線模式前，先用原設定執行 docker compose down，再修改 profile 與模型位址並重新啟動，確保先前啟用的通道一併停止。

提供 data/ssh/model_key 與已核對 fingerprint 的 data/ssh/known_hosts，權限 0600，owner 與 STUDYDY_UID 相符。若已有可用的金鑰與 known_hosts，可在 .env 設定 STUDYDY_SSH_KEY_FILE 與 STUDYDY_SSH_KNOWN_HOSTS_FILE 的絕對路徑，直接沿用原檔而不另存私鑰副本。通道只唯讀掛載這兩個指定檔案，不自動接受未知 host key，也不掛載整個主機 .ssh 目錄。

SSH 通道需要遠端的互動式 POSIX shell 與 Python 3，將固定模型路由送到遠端 loopback 的模型 port，Bearer key 從遠端 VLLM_API_KEY 取得。它不依賴 root 提示字元，啟動／健康檢查不連模型，未知結果的請求不自動重播，也不對主機開放通道 port。後端不依賴外部模型或通道才能啟動；up --wait 會等候已啟用的通道健康檢查通過。

預設部署不啟動 SSH、不要求 Pod 設定檔，也不接管外部模型生命週期。

位址與 token 由目前部署設定提供，不回寫封存的 runtime lock／binding；已保存的內容 hash 不因部署位置改變而重算。模型身分與能力仍照原契約驗證。

## 資料布局

~~~text
repository/
  .env                         私密部署設定
  compose.yaml                 完整部署，SSH 為選用 profile
  compose.test.yaml            獨立測試環境
  data/
    artifacts/                 原檔、PDF、mapping、analysis archive
    models/unlimited-ocr/      固定 OCR snapshot
    postgres/                 PostgreSQL 持久資料
~~~

Logs 使用 docker compose logs，程序生命週期由 Docker 管理，不另造 host PID 系統。容器映像與 build cache 由 Docker 自己管理，不放入 data。

## 驗證與排錯

~~~bash
docker compose -p studydy-unit-tests -f compose.test.yaml run --build --rm unit
~~~

此命令在無網路、無產品資料掛載的容器內執行後端表層與 local AI 測試，不需要模型。完整回歸及 browser 見 [測試](testing.md)。

| 問題 | 檢查 |
| --- | --- |
| Compose 缺少變數 | 檢查 .env 的必要欄位；不要把展開後含密碼的 config 輸出到一般 log |
| 後端啟動失敗 | 查看 backend／init logs，核對資料 owner、DB 密碼、模型 URL 與 namespace 政策 |
| GPU device／vendor 無法選取 | 主機 GPU 驅動與 Docker GPU 整合；不要在容器中安裝主機驅動 |
| CDI 裝置無法解析 | 檢查 nvidia-ctk cdi list、Docker CDI 支援，以及 nvidia-cdi-refresh.service 是否成功產生設定 |
| OCR_DOWNLOAD_FAILED | 網路、目標是否為未知非空目錄、lock revision；不刪除既有權重來強迫通過 |
| SSH 通道啟動失敗 | SSH profile、主機設定、model_key 與 known_hosts 的檔案／權限 |
| AI 操作失敗 | OCR 權重、模型服務位址與連線及實際模型契約；使用 SSH 時位址須指向 model-bridge |

真實 runtime verify 會載入 OCR 並連線語意服務，需明確選擇執行，不是一般健康檢查：

~~~bash
docker compose exec backend \
  /app/backend/.venv/bin/python -m runtime.local_runtime verify
~~~

## 從既有安裝切換

先在獨立 Compose project、不同 port 與測試 data 驗證。正式切換前停止產品寫入，備份原 DB、artifact store 與私密設定；把 DB 備份還原到新 PostgreSQL，搬移 artifact store 並核對資料／檔案，再切換服務。

不要把新建空 DB 當成完成遷移，也不要直接搬動正在使用的 PostgreSQL 資料目錄。確認新環境可讀、可恢復且其他 checkout 沒有引用後，才能清除不用的舊腳本、目錄與套件。資料搬移、主機 GPU 配置及正式切換需分開審核，不能由 build 成功推定已完成。

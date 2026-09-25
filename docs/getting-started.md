# 安裝與啟動

[文件入口](../README.md) · [測試](testing.md) · [限制](limitations.md)

部署使用 Docker Compose，路徑以這份 repository 為基準，目錄不必叫 main。應用、Python、Node、LibreOffice、字型與 OCR 套件由映像提供；主機不需要另外安裝這些應用套件。

## 主機需求

- Linux 容器環境、Docker Engine 與支援 gpus 設定的 Docker Compose；目前驗證平台為 x86_64 Linux／WSL2。
- 完整 AI 流程需要 NVIDIA GPU、主機驅動及可用的容器 GPU 整合。WSL2 也需要正確配置 GPU 接入；僅在 WSL 中可用 nvidia-smi 不代表容器已能使用 GPU。
- Kernel／主機安全政策必須允許容器內的 unprivileged user namespaces。後端使用 [bubblewrap seccomp 規則](../ops/docker/bubblewrap-seccomp.json)，不使用 privileged、host PID 或 Docker socket。
- data 所在檔案系統須支援 Linux owner 與 mode；模型及 DB 需要足夠磁碟空間。

GPU 設定依 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) 或 [Docker Desktop WSL2 GPU 文件](https://docs.docker.com/desktop/features/gpu/) 核對；這屬主機配置，不由 Compose 自動安裝或修改。

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
| COMPOSE_FILE | 預設 compose.yaml；加入 :compose.gpu.yaml 或 :compose.ssh.yaml 選擇需要的服務 |
| STUDYDY_PORT | 對外前端 port，預設 4173 |
| STUDYDY_PUBLIC_ORIGIN | 瀏覽器的完整 origin，須與 port／網址一致 |
| STUDYDY_SECURE_COOKIE | 本機 HTTP 用 false；經 HTTPS 公開時用 true |
| STUDYDY_DATA_DIR | 預設 ./data；測試必須指定自己的資料目錄 |
| STUDYDY_UID／STUDYDY_GID | 後端及教材檔案 owner；預設 1000，可用 id -u／id -g 核對 |
| POSTGRES_DB／POSTGRES_USER／POSTGRES_PASSWORD | 本部署的 PostgreSQL 設定 |
| STUDYDY_SEMANTIC_BASE_URL | 模型服務 origin，只含協定、host 與可選 port，不加 /v1 |
| VLLM_API_KEY | 選用的模型 Bearer token；空值不送 Authorization |

模型位址可用 HTTP 或 HTTPS，不能把帳密塞進 URL。外部服務宜使用 HTTPS。前端不會收到模型憑證。

## 2. 啟動核心服務

~~~bash
docker compose up -d --build --wait
docker compose ps
~~~

預設入口是 http://127.0.0.1:4173。只有前端對主機開 port；Nginx 把 /v1 轉送到後端。API 定義可從 http://127.0.0.1:4173/v1/openapi.json 讀取。

Compose 的 init 只建立資料目錄及必要權限，不清空已有內容。PostgreSQL 就緒後，後端用原 migration runner 核對／套用 schema，再啟動 API 與 worker。重跑不重建已有帳號、教材或 schema。

核心映像可使用帳號、資料讀取、教材上傳與轉檔。AI 分析／出題仍需要下一節的 OCR 環境與相符的語意服務；不提供模型 fallback。

## 3. 啟用 OCR 與 GPU

GPU 映像另外封裝 runtime lock 指定的 PyTorch、CUDA 使用者層、Transformers 與 OCR adapter。主機 GPU 驅動不包含在映像中。

~~~bash
docker compose -f compose.yaml -f compose.gpu.yaml build
docker compose -f compose.yaml -f compose.gpu.yaml run --rm download-ocr
docker compose -f compose.yaml -f compose.gpu.yaml up -d --wait
~~~

download-ocr 從 [baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR) 下載 lock 指定的 revision，不載入模型。它只寫 data/models/unlimited-ocr，允許續傳自己標記的相同 revision，不覆寫未知的既有模型目錄。此命令不要求 GPU 裝置，但需要網路與模型磁碟空間。

已有核對過的相同 snapshot 時，可直接把完整權重放在該模型目錄，無須重新下載。後端以唯讀方式掛載權重，依賴均使用容器內 OCR 環境。

啟用 GPU 後，日常 Compose 命令都帶同一組 -f 檔案：

~~~bash
docker compose -f compose.yaml -f compose.gpu.yaml ps
docker compose -f compose.yaml -f compose.gpu.yaml logs --tail 100 backend
docker compose -f compose.yaml -f compose.gpu.yaml down
~~~

down 不刪除 bind-mounted data。不要把刪除 data 當成重新啟動方式。

## 4. 連接語意模型

RunPod 是選用供應商。Gemma 可以放在另一個容器、自有伺服器或其他 GPU 平台，只要符合 [runtime-lock.json](../local_ai/runtime-lock.json) 的模型、revision、server、tokenizer 與生成契約。

STUDYDY_SEMANTIC_BASE_URL 是容器實際連線位址。容器內的 127.0.0.1 指向容器自身，不能用它代指主機或另一個服務。host.docker.internal 會指向主機 gateway，但主機服務仍需監聽容器可達的介面；只綁主機 loopback 的服務不能直接靠改名稱存取。

若需 SSH，可在 .env 把 COMPOSE_FILE 設為 compose.yaml:compose.gpu.yaml:compose.ssh.yaml（不需 OCR 時省略 GPU 檔案）。設定 STUDYDY_SSH_HOST=user@host、STUDYDY_SSH_PORT，以及遠端模型的 STUDYDY_SSH_MODEL_PORT。之後直接使用 docker compose up／down／logs，無須每次重複 -f。

提供 data/ssh/model_key 與已核對 fingerprint 的 data/ssh/known_hosts，權限 0600，owner 與 STUDYDY_UID 相符。通道以唯讀方式掛載兩個檔案，不自動接受未知 host key；不要複製整個主機 .ssh 目錄進容器。

SSH 通道需要遠端的互動式 POSIX shell 與 Python 3，將固定模型路由送到遠端 loopback 的模型 port，Bearer key 從遠端 VLLM_API_KEY 取得。它不依賴 root 提示字元，啟動／健康檢查不連模型，未知結果的請求不自動重播。SSH overlay 會把後端模型位址設為容器內 model-bridge，不對主機開放通道 port。

預設部署不啟動 SSH、不要求 Pod 設定檔，也不接管外部模型生命週期。

位址與 token 由目前部署設定提供，不回寫封存的 runtime lock／binding；已保存的內容 hash 不因部署位置改變而重算。模型身分與能力仍照原契約驗證。

## 資料布局

~~~text
repository/
  .env                         私密部署設定
  compose.yaml
  compose.gpu.yaml
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
| OCR_DOWNLOAD_FAILED | 網路、目標是否為未知非空目錄、lock revision；不刪除既有權重來強迫通過 |
| AI 操作失敗 | GPU override、OCR 權重、模型服務連線及實際模型契約 |

真實 runtime verify 會載入 OCR 並連線語意服務，需明確選擇執行，不是一般健康檢查：

~~~bash
docker compose -f compose.yaml -f compose.gpu.yaml exec backend \
  /app/backend/.venv/bin/python -m runtime.local_runtime verify
~~~

## 從既有安裝切換

先在獨立 Compose project、不同 port 與測試 data 驗證。正式切換前停止產品寫入，備份原 DB、artifact store 與私密設定；把 DB 備份還原到新 PostgreSQL，搬移 artifact store 並核對資料／檔案，再切換服務。

不要把新建空 DB 當成完成遷移，也不要直接搬動正在使用的 PostgreSQL 資料目錄。確認新環境可讀、可恢復且其他 checkout 沒有引用後，才能清除不用的舊腳本、目錄與套件。資料搬移、主機 GPU 配置及正式切換需分開審核，不能由 build 成功推定已完成。

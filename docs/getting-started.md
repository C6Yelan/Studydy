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

## 本機 Podcast provider

Podcast 使用獨立 worker，避免語音生成佔用教材分析／題組排程。帳號內保存固定版本的選材、逐字稿與分集 WAV，音訊沿用私人 artifact store。建立後自動生成；快速與完整模式均保留全部所選重點，內容較多自動拆集。失敗／取消可接續已保存進度。

目前文字 provider 是本次測試授權的 `codex exec -m gpt-5.6-luna`，先產生逐段講解或雙人輪次，再獨立核對每段對應的來源；有具體核對錯誤時最多修正一次。CLI 使用主機既有登入、ephemeral 與唯讀空目錄，停用 shell、網頁搜尋、apps、plugins 與多 agent。不掛載 CLI 憑證進產品容器，不改寫教材原本的 Gemma binding。每集的單次生成／重試最多四次 CLI 請求，每次至多 120 秒；腳本核對仍未通過會顯示失敗。模型只回傳有界來源索引，由程式綁回原始 claim ID。對照表格時會帶入同一固定 KS、所引用頁面的原始區塊與位置，補足欄列標題及省略主語；不改寫原 claim／Evidence 關係。新腳本可加入明示為假設的故事或比喻，但示例的技術行為必須符合來源；不把虛構情境當成真實案例。單人與雙人皆使用統一的 turns 格式。0009 migration 一次轉換既有單人講稿，不保留舊格式讀取分支；文字、引用與音訊身分維持不變。

語音在本機執行 CosyVoice 3（`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`）的官方 RL 權重，輸出 24 kHz PCM WAV。正式採用已選定的 B 參考聲線與固定角色 seed，使用通用中英文正規化；英文縮寫／複合詞依詞典與類型分段處理。數值保留原值，MB／Mb 分別讀 megabytes／megabits，速率讀 per second，不展開成中文大數量。未知識別符與部分縮寫仍可能不自然，需以真實教材聽感持續修正。

雙人講稿以整集包含學習者與講解者為限制，每個來源允許 1–8 輪，無須逐來源問答；同角色連續發言只在合成時合併，原講稿來源關係保留。來源檢核失敗不發布。文字 provider 仍為 Luna，未切換 Pod Gemma。

provider 使用 `data/podcast/runtime/`；語音使用 `data/podcast/cosyvoice-b-runtime/`，其 `.pth` 沿用唯讀的原 `cosyvoice-runtime` 依賴並加入 [requirements.txt](../ops/podcast/requirements.txt) 的 B 正規化套件，不修改兩版共用 `backend/.venv`。每次子程序結束釋放 GPU；540 秒逾時或失敗不發布部分音訊。權重、官方 source、B 參考聲線與正規化快取位於 `data/models/podcast/` 的 `cosyvoice3/`、`cosyvoice-source/`、`cosyvoice-b-voices/`、`normalizer/`。模型與 source 來源：[CosyVoice 官方 repository](https://github.com/QwenAudio/CosyVoice)。

私有 `data/podcast/provider.env` 設定 `STUDYDY_PODCAST_TTS_PYTHON`（保留 venv Python 入口）、`STUDYDY_COSYVOICE_SOURCE_DIR`、`STUDYDY_COSYVOICE_MODEL_DIR`、`STUDYDY_COSYVOICE_VOICES_DIR`、`STUDYDY_COSYVOICE_NORMALIZER_CACHE` 及主機 CUDA 12.8 的 `LD_LIBRARY_PATH`。既有 Podcast 保留原音訊；需新建 Podcast 才能完整使用新講稿與聲線。公開比較頁已移除。

主機服務設定見 [studydy-podcast.service](../ops/podcast/studydy-podcast.service)，載入私有 `data/podcast/provider.env`。正式版 `.env` 的 `STUDYDY_PODCAST_PROVIDER_URL` 指向 `http://host.docker.internal:18010`，`STUDYDY_PODCAST_PROVIDER_TOKEN` 與主機服務的 token 相同；至少 32 字元。provider 只監聽 Docker gateway `172.17.0.1`，不公開 port，也不经过 Cloudflare。不同主機須核對 gateway 與 service 中的絕對路徑。

```bash
systemctl --user status studydy-podcast.service
systemctl --user restart studydy-podcast.service
```

未設定 provider 或服務不可達時，新生成會如實失敗，既有保存內容仍可讀取。啟動與健康檢查不代表文字或語音品質驗收；來源的 `needs_review` 狀態及缺頁提示仍保留。播放位置只存當前帳號在這台瀏覽器的 localStorage，沒有跨裝置同步。登出／換帳號會停止音訊並移除播放器來源；刪除 Podcast 會刪除其音訊與逐字稿，不刪原教材。刪除教材則同時移除其 Podcast。

## F1／F2／F3／F5 本輪 Luna 替代測試

設定 `STUDYDY_TEXT_TEST_PROVIDER=codex-exec-luna` 時，新教材分析、檢核及題組文字請求使用主機 provider 的 `/semantics`，大型分析／檢核單次允許 300 秒；Podcast 與短問答仍為 120 秒。新工作保存 `gpt-5.6-luna`／`codex-exec:luna-test/v1` 身分與獨立 lock hash，不覆寫既有 Gemma 的來源、快照或 hash；repository 的原 runtime lock 不改。依本機模型目錄保存 Luna 272K context，使用 o200k 的估計 token 數與 120K 輸入門檻，保留逾半容量給差異、指令與輸出；不冒充 Luna 或 Gemma 的精確 tokenizer。同 provider 的容量擴大可接續已保存批次，舊工作 hash 不變。移除此設定可恢復原模型設定；未完成 Luna 工作不可用 Gemma 身分接續。

語音問答共用 Podcast 的 CosyVoice TTS；本機 STT 使用 faster-whisper large-v3-turbo（CPU int8）與獨立 `data/voice/runtime`。provider.env 設定 `STUDYDY_STT_PYTHON`、`STUDYDY_STT_MODEL_DIR`。解碼使用 PyAV，單次最長 120 秒；錄音在 worker 處理後清除。對話、短回答音訊與工作狀態由 PostgreSQL 保存，無公開音訊 URL；問答及研究各有獨立 worker，不阻塞原教材 worker。provider 對文字、TTS、STT／對齊各自串行，避免同類工作互搶資源；忙碌或失敗如實回報並保留可重試狀態。

0010／0011 migration 新增語音對話與研究資料；舊 KS 清理需保留對話引用。升級後不可直接退回不認識新 migration 的舊後端，應使用相容 schema 的前向修復，保留備份與舊映像。持久資料及模型不納入 Git。

0012 為 Evidence 的無損 JSONB 儲存增加讀取能力界線。真實論文的字型擷取可能包含 U+0000；PostgreSQL JSONB 不能直接保存，因此僅在遇到零字元時使用可逆字串編碼，讀回後完整還原，再驗證 canonical document 與 hash。既有文件不改寫，頁碼、區塊、Evidence／Claim 身分及原始 PDF 保持不變。相同處理涵蓋會保存來源文字的 Podcast、問答與題組欄位。這解決儲存限制，不代表特殊公式的呈現或教學解讀已驗收。

F5 共用本機 Whisper，對齊原講稿與實際 WAV 的詞時間點；拼音只用於處理 ASR 同音字，不改寫顯示文字或引用。`source-scenes/v2` 用長連續語句確認段落身分，再用同段最早匹配詞的實測時間作為邊界，避免前段短句辨識差異造成畫面延後；保存兩組可回查錨點，不按字數猜測時間。`ops/podcast/stt-requirements.txt` 記錄辨識／對齊環境依賴。每集只保存時間範圍、可回查錨點及白名單場景，不保存影格影片。比較／流程使用既有內容及 Luna 的來源核對；來源不足改用原重點，provider 或時間對齊失敗則如實保留失敗狀態。場景與音訊各自處理，失敗不改掉已完成的 Podcast。

0013 新增主題草稿／批准紀錄，讓 F2 可從尚無教材的已批准主題搜尋；來源選定後才建立教材。0014 新增每集同步資料，依附 Podcast 並綁定講稿及音訊 hash；刪除 Podcast 同時清除場景，晚到結果不復活。兩者均需識別新 migration 的後端，回退採相容 schema 的修復方式。

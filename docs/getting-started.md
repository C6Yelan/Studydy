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

文字 provider 沿用 `codex exec -m gpt-5.6-luna`。新稿為 `podcast-script/v2`：`segments` 是 teaching beats，一個 beat 可整合多個相關 claim，同 claim 也可跨 beat 延續。turn 的文字 parts 引用 episode 的 `source_index` 與所屬 `evidence_ids`，朗讀 `text` 由 parts 串接並核對一致；不能以 claim ID 合併不同來源位置。全部來源須實質涵蓋，KS／claims／Evidence 仍是 canonical authority。頁面 context 只補原引用的表格標題或主語，不能加入 claim 外的知識。

每份候選稿經獨立 review inference 回傳 `correctness` 與 `teaching_quality` 兩個 blocking verdict；任一失敗不得發布，最多重寫一次，重寫後兩者都重新核對。每集單次生成／重試最多四次文字請求，每次至多 120 秒。CLI 使用既有登入、ephemeral、唯讀空目錄並停用工具，不改寫原教材 Gemma binding；真實模型呼叫仍須有當次素材及費用授權。來源不足、provider／儲存失敗不視為成功。舊 turns 腳本保留原 bytes／hash，由唯讀來源投影相容，不批次改寫舊 JSON。

語音在本機執行 CosyVoice 3（`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`）的官方 RL 權重，輸出 24 kHz PCM WAV。正式採用已選定的 B 參考聲線與固定角色 seed，使用通用中英文正規化；英文縮寫／複合詞依詞典與類型分段處理。數值保留原值，MB／Mb 分別讀 megabytes／megabits，速率讀 per second，不展開成中文大數量。未知識別符與部分縮寫仍可能不自然，需以真實教材聽感持續修正。

雙人只要求整集有兩個角色，允許短問題、單角色 beat、連續發言與自然交接。來源正確及教學品質分別核對，避免固定問答、無意義附和及同義重複。

Podcast 在 `synthesize.py` 組裝階段裁去語句 chunk 的首尾靜音（20ms RMS 視窗、40ms 保護區，保留內部停頓），再依語言接續、標點、角色及 beat 邊界加入停頓。最終 WAV 保存前由 `audio_mastering.py` two-pass FFmpeg loudnorm 處理，目標 −19 LUFS／−2 dBTP；成品量測容許 ±1 LU、true peak ≤ −1.9 dBTP，輸出仍是 24kHz mono PCM16。影片另量測 AAC 解碼結果，要求 ±1 LU／≤ −1 dBTP。量測及 policy 隨 artifact metadata 保存，失敗不發布；所有對齊使用 mastering 後的 WAV。Voice `/audio` 不套用 Podcast 停頓政策。

FFmpeg 優先使用本機 executable 或 imageio-ffmpeg，否則由既有 `STUDYDY_VIDEO_PYTHON` 找到 renderer 的 FFmpeg；不需更換 TTS 模型或修改共用 venv。

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

## 純平面影片與講稿時間軸

`GET /v1/podcasts/{id}/episodes/{index}/timeline` 將已完成的語音對齊投影為 `podcast-transcript-timeline/v1`。
以秒表示段落 `start/end`（起點含、終點不含），保留原文、發言角色、claim／Evidence、來源連結、音訊及講稿 hash。
`granularity=segment` 表示只核對段落時間，`turns` 內沒有推估的發言或逐字時間。尚未對齊回 409，必須先沿用同步畫面準備流程完成對齊。
經逐句分鏡及 ASR 核對的集數，scene manifest 另保存 `script_alignment`；API 回傳 `granularity=script_cue`，依講稿的解說單位提供時間，不以教材段落分鏡。
每個新片段以 `source_refs` 保留原 beat／發言索引與 Unicode 字元範圍（start 含、end 不含），從文字交集推導 claim／Evidence，不把整個 beat 的來源塞給每個 cue；既有單發言片段仍有 `source_segment_index/source_turn_index`。拼回各發言須逐字等於原稿。短提問與回答可共用一個 cue，但仍分別保存 speaker，不額外推算逐角色切換時間。音訊或講稿 hash 改變、時間不符語音錨點均拒絕讀取，不沿用過期切分。
同一路徑的 `/subtitles` 提供 WebVTT，兩個端點都沿用原 Podcast 權限與私人快取政策。時間軸使用已保存的對齊資料，不改寫原講稿；完成影片時優先讀取影片綁定的時間軸。

每集新音訊完成後自動排入影片佇列；v2 腳本另自動排入既有 beat alignment 工作，先提供基本 timeline，不必等 MP4；既有集數可在播放器補產生，失敗可重試，處理中可取消。影片失敗不影響原音訊。
正式播放器提供影片／音訊切換、投影片跳轉、同步講稿及 JSON／WebVTT 下載，延續原 Podcast 的帳號權限。

0015 migration 新增影片工作、來源指紋及 artifact 關係。影片只在來源仍一致、版面及來源核對通過且 artifact 寫入成功後標記完成。取消、刪除或過期工作的晚到結果不會發布。升級前備份 PostgreSQL；升級後以識別 0015 的版本前向修復。

`ops/podcast/video_service.py` 使用既有文字 provider 切分講稿、安排固定頁面圖形、在一次 review 分別核對 correctness 與 teaching-quality（兩者都 blocking），分鏡驗證失敗時將具體錯誤交回模型修正，版面錯誤與來源核對各保留兩次修正機會，任一類第三次失敗即停止（每次工作最多九次文字請求）；Whisper 在本機對齊原 WAV。`backend/src/runtime/podcast_video_render.py` 只接受白名單圖形，在 CPU 產生 1920×1080／60fps MP4，使用原音訊與實測時間，不執行模型程式碼。
新產生的 `flat-report/v4`／`podcast-storyboard/v2` 一頁只聚焦一個 mental model；每頁最多三組 `reveal`，引用既有 elements 索引與 cue 邊界，顯示後保留至頁尾。單位、必要條件與節點／連線須完整呈現，連線不得早於其節點；未加入 reveal 的元素頁首顯示。`cue_index` 仍表示開始解釋的時機。畫面底部只放短焦點提示，完整字幕保留 WebVTT，由播放器的字幕按鈕切換，不將逐字稿重複燒進新影片。舊 MP4、舊 `flat-report/v3` manifest 與靜態顯示方式原樣保留。
每頁 `emphasis` 只引用既有元素與 cue：底線、描邊框選或沿既有連線移動的講解指示點。只在有助理解時標示，可整頁沒有標記，不設配額。標記逐步畫出，片段結束前淡出，底圖保留；同時最多兩處，可比較同一元素中的不同詞，不能重複覆蓋相同詞或提前強調尚未講解的內容。模型不決定任意秒數、也不增加標記文字；時機由實際語音對齊決定，另經來源核對。既有 MP4 不會因程式更新而自動重製。

影片的細切若無法獨立取得可靠錨點，僅在同一原發言內合併相鄰片段；完整辨識的短回覆或共用 ASR 詞時間也可保留角色後合併。合併不遺失文字、不跨越不可靠的完整發言，仍要求至少 8 字的連續語音錨點、實測時間與字幕長度上限。F5 原段落對齊不啟用此合併，音訊不重製。

渲染環境依賴見 `ops/podcast/video-render-requirements.txt`，字型為 Noto Sans CJK。provider.env 設定 `STUDYDY_VIDEO_PYTHON`（獨立渲染環境）、`STUDYDY_VIDEO_WORK_DIR`（暫存目錄）、`STUDYDY_VIDEO_MIN_FREE_BYTES`（最低可用容量）及可選的 `STUDYDY_VIDEO_HOST_FREE_PATH`（宿主磁碟容量檢查）。影片工作串行，暫存檔處理後清除；不用下載影片生成模型。來源不足或驗證失敗會保留失敗狀態，不視為通過。

已認可的原節奏樣片可保留實際來源與時間軸作為既有產物；單一樣片不代表任意教材均已驗收。Gemma 分鏡尚未驗證。臨時比較頁及專用產生腳本已移除。


## Podcast 互動與升級邊界

逐字稿與同步圖卡皆使用 Podcast 建立時的 revision resolver 回查原教材。問目前播放段落會暫停播放器、固定該次 locator 並開啟既有 Voice；尚無可靠 alignment 時只允許手動選段，不猜目前 beat。Voice 新對話可指定 exact revision；文字問答的 `context`／錄音的 `X-Studydy-Podcast-Context` 只傳 Podcast ID、episode index、script hash 與文字範圍。後端確認 ownership、revision、範圍及引用，回答只依同 revision canonical claims，context 不成為知識來源。錄音確認、重試與取消沿用原工作生命週期。

0016 migration 僅新增 nullable `voice_turns.context`。欄位保存 locator 與必要來源引用，不複製 script；Podcast 刪除後拒絕使用該 locator 新追問，既有回答仍依 Voice 原本的保存規則保留。無 Podcast／audio／MP4／manifest 資料搬移或自動重生成；升級須一起更新 provider、backend 與 frontend，並使用識別 0016 的後端前向修復。本次開發不代表已對產品執行 migration 或部署。

末集播放結束（即使關閉自動接續）及手動入口都能進入原 revision 的既有 Assessment。使用 canonical study session 與 focus API，不新增 Podcast 題組或 mastery。已完成 session 僅開啟該概念已有的題組；沒有題組時引導回原版本地圖，不重新開啟完成狀態。播放、seek、聽完及 Voice 問答皆不產生 mastery 作答事件。

30fps 只供 benchmark 輸入使用；正式 bundle 仍要求 1080p60。benchmark 結論及重跑命令見 [測試](testing.md)。原教材圖片／page-region asset、3D、生成影片與模型 executable code 均不在此版本範圍。

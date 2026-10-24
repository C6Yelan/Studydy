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
| STUDYDY_UID／STUDYDY_GID | 主機資料管理者與 bridge／tunnel／下載工具身分，預設 1000；GID 維持 1000 |
| STUDYDY_BACKEND_UID | 後端與 OCR 子程序的專用 UID，預設 20001 |
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

Gemma 的專案預設 context 為 65,536 tokens，包含輸入與輸出；服務端須配置相同的 `--max-model-len 65536`，preflight／tokenize 仍核對服務實際回報。新教材分析採獨立子圖，每批新增輸入目標為 4,096 tokens、最多八路併行，單一原文 block 不截斷；已保存的 v1 工作仍保留原 1,536-token 契約。輸出上限維持 32,768，教材至少保留 8,192、評量至少保留 16,384 tokens 的輸出空間。同模型與相同契約僅從 32K 擴為 64K，可重用舊分析 checkpoint，舊 hash 不改寫；降容量或換模型不能沿用此例外。這是專案容量設定，不代表遠端 GPU 已重啟或 VRAM 滿載驗收通過。

init 建立資料目錄及權限；後端依序核對 migration checksum、套用待執行版本，再啟動 API 與 worker。重跑不重建已有帳號或清空資料。登入、已保存資料讀取、上傳與轉檔不需語意模型。

初始化 SQL 的 0001–0007 為身份／教材、來源／分析、學習／評量、研究、卡片／媒體、讀取索引與跨領域外鍵，直接建立所需結構，不重播歷史資料轉換。後續 0008–0015 分別加入個人顯示名稱、概念卡學習標記、Podcast 收聽進度、教材新版承接、知識章節、登入裝置清單、起始程度／跨概念診斷與學習目標；實際檔案以 [migration 目錄](../backend/migrations/) 為準。

新初始化 prefix 一經發布便固定，後續 Schema 變更繼續追加 SQL。

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

Evidence 的無損 JSONB codec 僅在遇到 U+0000 時使用可逆字串編碼，讀回後完整還原，再驗證 canonical document 與 hash。既有文件、頁碼、區塊、Evidence／Claim 身分及原始 PDF 不改寫；相同處理涵蓋保存來源文字的 Podcast、問答與題組欄位。這解決儲存限制，不代表特殊公式的呈現或教學解讀已驗收。

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

Podcast 使用獨立 worker，避免語音生成佔用教材分析／題組排程。帳號內保存固定版本的選材、逐字稿與分集 WAV，音訊沿用私人 artifact store。建立後自動生成，只選擇單人解說或雙人對談，兩者均保留全部所選重點，內容較多自動拆集。失敗可接續已保存進度；取消會刪除整份 Podcast。分集沿用所選概念順序，在每集最多 32 個 claim／2,400 來源字元的硬容量內，優先保留完整概念及既有 Relation／Evidence 的連續性，再考慮集數和容量平衡；不重排或刪減來源。單一 claim 超過容量時明確拒絕，不截斷它。

文字 provider 沿用 `codex exec -m gpt-5.6-luna`。新稿為 `podcast-script/v2`：`segments` 是 teaching beats，一個 beat 可整合多個相關 claim，同 claim 也可跨 beat 延續。模型只選 `source_index` 與該來源內的 `evidence_indices`；程式核對範圍並回填原 ID，保存的 turn 文字 parts 引用 episode 的 `source_index` 與所屬 `evidence_ids`，朗讀 `text` 由 parts 串接並核對一致；不能以 claim ID 合併不同來源位置。全部來源須實質涵蓋，KS／claims／Evidence 仍是 canonical authority。頁面 context 只補原引用的表格標題或主語，不能加入 claim 外的知識。

新稿依來源內容量使用同一套整集字數與 beat 上限；雙人只增加交接所需的 turn 空間，不額外放大字數。英文依詞而非逐字母估量，同一長段被多個 claim 引用時不重複增加預算；這只影響容量，全部來源仍須實質涵蓋。篇幅容量包含具體推演、例子與自然問答，不再以接近來源摘要的長度壓縮講解。生成提示提供完整教學的參考字數／時長，驗收不以湊滿字數為準，上限也不是最低篇幅。新稿保存前由 backend 再檢查統一預算，已保存的 v2／legacy 講稿不重新套用。新生成標示 `teaching-beats/v10`；產稿與 correctness 審查另核對跨句指代及階段，來源混有不同流程時以明確動作取代可能誤接條件的「這個步驟」，且不以刪除另一階段的已選命題迴避歧義。建立 API 不再接收 `mode`，列表與詳情也不回傳 Quick／Full；DB 的舊欄位只保留歷史資料，新建固定儲存值，不參與生成或審查決策，沒有資料搬移或重製既有產物。

超出容量仍直接要求修稿；短附和、字面重述、確認問句、規律輪替及 recap 都只作為可定位的風格訊號，交給獨立教學審查結合上下文判斷，不再自動拒絕。重述可用於預測、修正理解、對照及應用整理；不要求每句都新增事實。若實際缺乏教學作用，審查須指出具體 turn 與原因，不能以文風偏好擋稿。

寫稿與審查只取得已引用的原始區塊；孤立數值另帶入同欄、上方緊鄰的短標題，供欄位消歧義。不能因能讀到同頁其他段落，就擴寫未選的機制。表格標題、合理同義改述與直接整合可以使用；新增技術條件、保證或錯誤數值仍由 correctness 拒絕。

不帶來源斷言的純提問 beat 會在容量允許時，與緊接的解答合併為同一教學單位；不改任何發言或 part 引用。審查須區分「重用同一 claim」與「重述同一命題」，不能拒絕該來源尚未說過的新細節。

符合上述檢查的候選稿經獨立 review inference 回傳 `correctness` 與 `teaching_quality` 兩個 blocking verdict；任一失敗不得發布，最多重寫一次，重寫後兩者都重新核對。每集單次生成／重試最多四次文字請求，每次至多 120 秒。CLI 使用既有登入、ephemeral、唯讀空目錄並停用工具，不改寫原教材 Gemma binding；真實模型呼叫仍須有當次素材及費用授權。來源不足、provider／儲存失敗不視為成功。舊 turns 腳本保留原 bytes／hash，由唯讀來源投影相容，不批次改寫舊 JSON。

語音在本機執行 CosyVoice 3（`FunAudioLLM/Fun-CosyVoice3-0.5B-2512`）官方 RL 權重，輸出 24 kHz PCM WAV。Podcast 沿用原 host／guest 參考錄音與固定角色 seed，採用已選定的 V／W `inference_instruct2` 台灣華語指令。`spoken-input/v13` 把同一句的中文、英文與縮寫一起合成，不再因語言切換而改用另一種聲音生成模式；英文冠詞 a 保留，縮寫依通用字母名稱處理，千分位與毫秒先正規化。MB／Mb 仍區分 megabytes／megabits，速率讀 per second。原講稿、來源與引用不改；未知識別符、byte／bit 與其他術語仍須真實聽評。Podcast 與語音問答共用的文字前處理會把可辨識的英文複合詞連字號當作詞間分隔，不再插入 hyphen；代碼、縮寫與數值的符號仍保留。此規則不依特定教材或術語。

新分集共用 `MAX_EPISODE_CLAIMS=32` 的來源上限，仍保留每集 2400 字來源容量及既有來源／教學審查。優先保留完整概念和關係，避免少量短 claim 把節目切碎；這不代表每集固定時長，也不能為了湊長度補造或重複內容。已保存的分集不重新切割。

講稿採節目形式（`source-script/v13`）：開場引子＋本集路線、段落串場，結尾 3～5 點回顧，並依下一集的概念名稱預告；系列最後一集不預告。雙人時 host 帶節奏（開場、串場、生活例子、替聽眾整理、收尾），guest 深入說明，兩人都懂內容，一起為聽眾講解，不是逐點一問一答；單人由一位主持人講完整集，套用相同結構。數字與公式口語只講做什麼、為什麼與結果並保留關鍵數字，完整算式留在「本集重點」的原文；prompt 禁止補來源沒有的邊界／等號條件、替未標示的數值加標籤、跨範例混用數值，來源互相矛盾的數字不念。這些靠 prompt 與人工審稿防範，程式的引用編號檢查抓不到「引用對、敘述錯」。建立 Podcast 時從 `podcasts.HOST_PAIRS` 抽一組主持人名字存入每集 `hosts`（host 對應女聲 host.wav、guest 對應男聲 guest.wav，單人只存 host），同系列各集相同；worker 送 `/script` 時附上 `hosts` 與 `series`（集數、總集數、下一集概念名稱）。節目化前已建立但尚未寫稿的集數，在領取時補抽一組名字，已完成的集數不改寫。整集仍以 9600 字、12 個 beat 為上限，開場與收尾也算在內；模型輸出的每個 beat 至多 12 輪，與語音合成的上限一致。

Podcast 在 `synthesize.py` 接收模型的已知句子邊界，交給 `dialogue_audio_repair.py` 處理。句首孤立非週期脈衝只在符合保護條件時移除，短有聲字詞及緊接母音的子音保留；10／20ms 淡入淡出消除硬接點。語速依中文發音量及 CMU 英文音節與實際音訊估計，小幅拉近偏離本集長句中位數的部分，保留音高；短語氣詞及未知發音不調速。句尾／問句／換人或 beat 使用 .24／.26／.32 秒停頓目標，扣除音訊本身的安靜區，不疊加靜音。規則不依賴特定字句、來源 ID 或時間點。

最終 WAV 仍由 `audio_mastering.py` two-pass FFmpeg loudnorm 處理，目標 −19 LUFS／−2 dBTP，成品容許 ±1 LU、true peak ≤ −1.9 dBTP。mastering schema／policy 不變；音訊 provider 身分包含 `rl-reference-vw;spoken-input/v13;dialogue-audio/v1`。失敗不發布部分檔案，所有字幕與畫面對齊仍以最終 WAV 為準。原音訊不批次改寫；需新建 Podcast 才會採用新版。

Podcast 的額外 AI 講稿／分鏡審查與自動重寫已移除，現行驗證範圍見 [測試文件](testing.md#podcast-現行驗證範圍)。音量正規化保留，量測偏差不再阻擋成品。

FFmpeg 優先使用本機 executable 或 imageio-ffmpeg，否則由既有 `STUDYDY_VIDEO_PYTHON` 找到 renderer 的 FFmpeg；不需更換 TTS 模型或修改共用 venv。

provider 使用 `data/podcast/runtime/`；語音使用 `data/podcast/cosyvoice-b-runtime/`，其 `.pth` 沿用唯讀的原 `cosyvoice-runtime` 依賴並加入 [requirements.txt](../ops/podcast/requirements.txt) 的 B 正規化套件，不修改兩版共用 `backend/.venv`。每次子程序結束釋放 GPU；Podcast 音訊生成不設固定完成期限，失敗不發布部分音訊。權重、官方 source、B 參考聲線與正規化快取位於 `data/models/podcast/` 的 `cosyvoice3/`、`cosyvoice-source/`、`cosyvoice-b-voices/`、`normalizer/`。模型與 source 來源：[CosyVoice 官方 repository](https://github.com/QwenAudio/CosyVoice)。

私有 `data/podcast/provider.env` 設定 `STUDYDY_PODCAST_TTS_PYTHON`（保留 venv Python 入口）、`STUDYDY_COSYVOICE_SOURCE_DIR`、`STUDYDY_COSYVOICE_MODEL_DIR`、`STUDYDY_COSYVOICE_VOICES_DIR`、`STUDYDY_COSYVOICE_NORMALIZER_CACHE` 及主機 CUDA 12.8 的 `LD_LIBRARY_PATH`。既有 Podcast 保留原音訊；需新建 Podcast 才能完整使用新講稿與聲線。公開比較頁已移除。

主機服務設定見 [studydy-podcast.service](../ops/podcast/studydy-podcast.service)，載入私有 `data/podcast/provider.env`。正式版 `.env` 的 `STUDYDY_PODCAST_PROVIDER_URL` 指向 `http://host.docker.internal:18010`，`STUDYDY_PODCAST_PROVIDER_TOKEN` 與主機服務的 token 相同；至少 32 字元。provider 只監聽 Docker gateway `172.17.0.1`，不公開 port，也不经过 Cloudflare。不同主機須核對 gateway 與 service 中的絕對路徑。

Podcast 的講稿、音訊、字幕與影片工作會將工作 ID／本次 lease 傳給 provider。介面的「取消並刪除」與刪除使用相同流程：停止整份 Podcast，刪除所有分集的講稿、音訊與影片，原教材保留，不留下暫停項目。worker 會通知 `/jobs/cancel`，provider 會中斷等候資源鎖的請求並終止整個子程序群組。清單與播放器另外呈現影片／字幕工作，避免音訊已完成時隱藏尚在生成的工作。失敗重試使用新 lease，舊取消訊號不會停止新工作。Podcast 音訊連線建立後不設固定等待期限，工作租約在 worker 存活時續期；15 分鐘租約只用來回收失聯 worker，不是生成時間上限。更新此協定時先更新 provider，再更新 backend；部署前取消進行中的工作並確認舊服務沒有殘留程序。這不代表遠端模型服務已受理的請求一定能撤銷計費。

Provider 將工作 ID、分集索引、操作、耗時、固定原因碼與例外類型寫入本機 `data/podcast/job-events.jsonl`，每份最多 1 MiB，輪替保留兩份備份。不記錄教材、講稿、工作 token、憑證或例外原文。



```bash
systemctl --user status studydy-podcast.service
systemctl --user restart studydy-podcast.service
```

未設定 provider 或服務不可達時，新生成會如實失敗，既有保存內容仍可讀取。啟動與健康檢查不代表文字或語音品質驗收；來源的 `needs_review` 狀態及缺頁提示仍保留。播放位置與收聽秒數存在伺服器（migration 0010），同帳號可跨裝置接續；瀏覽器舊有位置只在伺服器沒有紀錄時上傳一次。登出／換帳號會停止音訊並移除播放器來源；刪除 Podcast 會刪除其音訊與逐字稿，不刪原教材。刪除教材則同時移除其 Podcast。

## Luna 設定與語音問答

設定 `STUDYDY_TEXT_TEST_PROVIDER=codex-exec-luna` 時，新教材分析、檢核及題組文字請求使用主機 provider 的 `/semantics`。新工作保存 `gpt-5.6-luna`／`codex-exec:luna-test/v1` 身分與獨立 lock hash，不覆寫既有 Gemma 的來源、快照或 hash；移除此設定可恢復原模型設定，但未完成 Luna 工作不能以 Gemma 身分接續。

Luna 的後端總額度為 65,536 tokens，包含輸入與輸出，使用與 provider 相同的 JSON 序列化及 o200k 估算。送出前保留教材至少 8,192、評量至少 16,384 tokens 的輸出空間；回傳 JSON 超過剩餘額度時回報 `SEMANTIC_OUTPUT_TOO_LARGE`，不保存為成功。這是送出內容及回傳 JSON 的估算限制，不是 CLI 內部提示、schema、隱藏推理 token 或費用的精確總量；可沿用的舊工作 hash 與 checkpoint 不改寫，後續請求仍受現行容量檢查。

語音問答共用 Podcast 的 CosyVoice TTS；本機 STT 使用 faster-whisper large-v3-turbo（CPU int8）。provider.env 以 `STUDYDY_STT_PYTHON`、`STUDYDY_STT_MODEL_DIR` 指定獨立環境與模型；錄音在 worker 處理後清除；回答音訊保存在資料庫，經需登入的問答音訊端點讀取。問答與研究各有獨立 worker；文字、TTS、STT／對齊依 provider 的槽位與鎖排程，取消須傳至在途子程序，失敗如實回報。

問答分為文字與語音模式。新文字問題在教材回答完成後即可閱讀，不產生 TTS；新錄音辨識完成後自動回答、產生語音，無須再次送出，辨識文字保留在對話中。畫面顯示實際工作階段；只有本次錄音或明確重試的語音回答會嘗試自動播放。瀏覽器阻擋播放時保留完成的文字與音訊，提示「語音回答已完成，點擊播放」。切換模式、路由、帳號或取消會停止播放及清除自動播放意圖；重新載入不自動播放歷史回答。

`0005_cards_and_media.sql` 建立語音對話、問答與 nullable `voice_turns.mode`／`context` 欄位，新問題由文字／錄音入口決定模式。缺少有效 mode 或仍為 draft 的舊 turn 讀取時回報 `MATERIAL_FORMAT_UNSUPPORTED`，不猜測原輸入方式或改寫原資料；工程測試不代表真實 STT／TTS、麥克風或各裝置播放品質已驗收。

`0004_research.sql` 建立研究與主題草稿／批准資料，確認範圍後才搜尋，來源選定後才建立教材。知識結構清理須保留對話引用，回復程式時須使用相容於現存 schema 的版本；持久資料及模型不納入 Git。

同步場景共用本機 Whisper，對齊原講稿與實際 WAV 的詞時間點；拼音只用於處理 ASR 同音字，不改寫顯示文字或引用。`source-scenes/v2` 用長連續語句確認段落身分，再以同段匹配詞的實測時間建立可回查錨點，不按字數猜測時間；依賴見 `ops/podcast/stt-requirements.txt`。`0005_cards_and_media.sql` 的每集同步資料綁定講稿及音訊 hash，刪除 Podcast 時清除場景，晚到結果不得復活；對齊失敗不改掉已完成的原音訊。

## 純平面影片與講稿時間軸

`GET /v1/podcasts/{id}/episodes/{index}/timeline` 將已完成的語音對齊投影為 `podcast-transcript-timeline/v1`。
以秒表示段落 `start/end`（起點含、終點不含），保留原文、發言角色、claim／Evidence、來源連結、音訊及講稿 hash。
`granularity=segment` 表示只核對段落時間，`turns` 內沒有推估的發言或逐字時間。尚未對齊回 409，必須先沿用同步畫面準備流程完成對齊。
經逐句分鏡及 ASR 核對的集數，scene manifest 另保存 `script_alignment`；API 回傳 `granularity=script_cue`，依講稿的解說單位提供時間，不以教材段落分鏡。
每個新片段以 `source_refs` 保留原 beat／發言索引與 Unicode 字元範圍（start 含、end 不含），從文字交集推導 claim／Evidence，不把整個 beat 的來源塞給每個 cue；既有單發言片段仍有 `source_segment_index/source_turn_index`。拼回各發言須逐字等於原稿。短提問與回答可共用一個 cue，但仍分別保存 speaker，不額外推算逐角色切換時間。音訊或講稿 hash 改變、時間不符語音錨點均拒絕讀取，不沿用過期切分。
同一路徑的 `/subtitles` 提供 WebVTT，兩個端點都沿用原 Podcast 權限與私人快取政策。新對齊另保存 `caption_alignment`：以原 turn、標點和同一次 Whisper 的詞時間產生較短讀句，逐字拼回仍等於原稿；多個片段共用 ASR 時間時合併，不在詞內插值。timeline 的 `segments` 繼續負責來源、seek 和「問這一段」，字幕使用獨立的 `captions`，不切碎 teaching scope。未保存細字幕錨點的舊產物沿用原 VTT，不自動重跑模型或改寫 manifest。時間軸使用已保存的對齊資料，不改寫原講稿；完成影片時優先讀取影片綁定的時間軸。

每集新音訊完成後自動排入影片佇列；v2 腳本另自動排入既有 beat alignment 工作，先提供基本 timeline，不必等 MP4；既有集數可在播放器補產生，失敗可重試，處理中可取消並刪除整份 Podcast。影片失敗時仍保留原音訊。
正式播放器提供影片／音訊切換、投影片跳轉、同步講稿及 JSON／WebVTT 下載，延續原 Podcast 的帳號權限。

`0005_cards_and_media.sql` 建立影片工作、來源指紋及 artifact 關係。影片只在來源仍一致、版面及來源核對通過且 artifact 寫入成功後標記完成；取消、刪除或過期工作的晚到結果不會發布。

`ops/podcast/video_service.py` 先以 beat／turn／part 與標點決定 teaching cues，再由本機 Whisper 對齊原 WAV，切 cue 不呼叫文字模型。文字 provider 在同一次分鏡呼叫中描述語意分鏡。每段講稿（beat）輸出 1～3 頁，各頁獨立選版型；全集上限 24 頁，每段上限為 min(3, 24 ÷ 段數)，由 schema 保證。講稿的每個主題都要有對應的頁或卡，同一段先後講兩個版型不同的主題時拆頁，不丟主題也不把性質塞成步驟。第 2 頁起以逐字取自本段講稿的頁 `anchor` 決定換頁位置，換成段內實測起點（cue 或字幕開頭），子頁可從某句字幕開始並與前一頁共用該 cue；頁 anchor 對不到、倒退、不在本段或擠掉後頁所需的起點時，改放在前後換頁點之間居中的實測起點；段內實測起點少於頁數時，放不下獨立起點的頁把節點併入前一頁（沿用前一頁標題；同為 concept 或 flow 時保留版型，否則改為 concept 聚焦卡，exchange 訊息寫成「傳送方 → 接收方」），不丟內容；併入後放不下才回報 `VIDEO_LAYOUT_INVALID`。exchange 至少兩則訊息，只有一則或還有條件要說明時改用 concept；同一組各只有一句的短項目合成一張卡。頁碼、章節列表、換頁 crossfade 都依實測頁起訖計算。版型：concept 主重點與支持說明、comparison 比較群組、flow 有來源的相鄰步驟、exchange 兩個參與者間的往返訊息；輸出短文字與節點／關係索引，每個節點（exchange 為每則訊息）另輸出 `anchor`（逐字取自本段講稿、落在該頁範圍內、標示開始講這個節點的片段）與 0～2 個 `key_terms`（逐字取自節點文字的重點詞，優先技術名稱），不輸出 x/y/w/h、秒數或索引。`podcast_video_layout.py` 只採用逐字出現在本段講稿、且落在該頁範圍內的節點 anchor，換算成頁內實測起點（teaching cue 或其中字幕的開頭），不生成新秒數；對不到時不讓整份失敗：第一張卡固定從頁首開始，其後對不到或倒退的 anchor 改用前一張之後的下一個實測起點，comparison 退回頁首，exchange 與前一則訊息同時出現。頁內實測起點少於節點時（例如字幕只有段落粒度），相鄰節點依序共用一張卡。重點詞必須逐字出現在節點說明（說明裡找不到時才改找標籤的一部分），以中文字寬計不超過 16 字（英數字算半個）、不可等於整段文字，單一字母／符號與單獨數值（例如 `L`、`0.09`）不標，重疊或不符的詞直接丟掉，只標該詞第一次出現的位置；英數開頭或結尾的詞要有詞邊界，前後緊接英數字或 `-_.:/=` 時不算（例如不把 `CDN-Cache-Control` 開頭的 `CDN` 標出），改找下一個合格位置，都沒有就丟掉。中文詞不受此限。新工作以真實字型量測編譯成 `podcast-storyboard/v4`：concept／flow 為聚焦版面（左側章節列列出本頁所有節點並以待講／正在講／已講完三態顯示，flow 以連線表示步驟順序；右側同一區域依講述時間只顯示正在講的那張卡，句子完整保留）；comparison 維持並排，依各欄 anchor 點亮正在講的那欄；exchange 維持參與者與箭頭圖，訊息依 anchor 出現。所有 v4 內容停在 y=850 以上，不蓋到播放器底部字幕；放不下回報 `VIDEO_LAYOUT_INVALID`，不縮到不可讀或截字。拉丁縮寫與單位能放進一行時不在詞內斷行，重複量測使用有界快取。

分鏡只生成一次，不另做 AI 內容審查或自動重寫（見[測試文件](testing.md#podcast-現行驗證範圍)）；版型不當成新的知識依據。沒有模型幾何不代表任何內容皆可容納，過長文字或無效語意索引仍會拒絕。

`backend/src/runtime/podcast_video_render.py` 沿用有限圖形與 1920×1080／60fps MP4（CPU 繪圖，H.264 可選 CPU／NVENC 編碼），不執行模型程式碼。reveals 由語意目標的 cue 編譯、同 cue 合併，顯示後保留至頁尾；相關節點不晚於連線出現，單位與必要條件仍由內容審查核對。v4 聚焦頁的卡片在下一張開始時短暫交叉淡化，章節列同步改變狀態；重點詞以同字級、同斷行的卡片主色字加淡色底呈現，於所屬卡片被講到時淡入。v4 文字行距為 1.5 倍字級，v2／v3 維持原行距。comparison／exchange 與 v3 相同，由 `podcast_video_motion.py` 以 manifest、實測 timeline 與目前媒體秒數直接計算狀態：節點及文字淡入、線條／箭頭 draw-on、焦點明暗與淡底平滑轉移，已出現的次要內容仍保留。flow／exchange 指示沿既有箭頭一次性推進並移交節點焦點；不循環、不表示實際封包速度。同一 anchor 的目標同時聚焦，不編造更細的先後。v3 relation 不再疊加第二個 trace 指示。換頁後使用短 crossfade，舊頁以邊界前的確定畫格計算，底部提示與進度仍取目前時間。固定的視覺時長上限皆受實測窗口限制；seek／倍速沒有前一影格的殘留狀態。舊 underline／outline／trace 圖形契約保留可讀，標記不設必填配額。

升級時先更新可接受 v4 的 backend，再更新／重啟 video provider（`video_service.py` 與 `podcast_video_*` 編譯／繪製模組同一份 checkout）。backend 在保存結果時仍接受 v2／v3 storyboard。provider 會直接載入同一份 checkout 的編譯模組，所以更新 checkout 後要立即重啟 provider；重啟前仍在執行的舊行程產生的分鏡會被新編譯器以 `VIDEO_STORYBOARD_INVALID` 拒絕，該集標為失敗、需重試，不會寫入壞資料。已完成影片的 MP4、timeline 與保存的 storyboard 讀取時不重新驗證，也不掃描、重排或重製 ready 產物。

新影片底部只放短焦點提示，完整字幕保留 WebVTT，由播放器設定中的字幕按鈕切換。播放器沿用瀏覽器 VTT cue 的實測時序，在可換行的字幕區呈現；字幕底緣依控制列高度與 viewport 調整，避免手機長字幕裁切或被播放控制遮住。既有 MP4、`flat-report/v3`／`v4` manifest 與原顯示方式不自動重製；來源、音訊、script hash、ownership 與晚到結果隔離仍沿用原規則。

影片對齊不設固定字數門檻。完整匹配沿用整體順序；部分匹配需能區分原稿與辨識中的位置。錨點仍逐字對回原稿，所有起點取自實際 ASR 詞時間，拒絕空白、倒序或越界的時間。共用詞時間的片段可以合併後重新核對，不按字數猜時間。字幕優先切成較短讀句；細字幕無法可靠切分時，沿用已驗證的教學段落時間，以 paragraph-captions/v1 記錄，不讓細字幕失敗阻止整集影片。主段落對齊失敗仍不發布影片，音訊不重製。

渲染環境依賴見 `ops/podcast/video-render-requirements.txt`，字型為 Noto Sans CJK。provider.env 設定 `STUDYDY_VIDEO_PYTHON`（獨立渲染環境）、`STUDYDY_VIDEO_WORK_DIR`（暫存目錄）、`STUDYDY_VIDEO_MIN_FREE_BYTES`（最低可用容量）及可選的 `STUDYDY_VIDEO_HOST_FREE_PATH`（宿主磁碟容量檢查）。`STUDYDY_VIDEO_ENCODER` 選擇 `libx264`（預設）或 `h264_nvenc`；後者需要設定 `STUDYDY_VIDEO_FFMPEG` 指向含 NVENC 的 FFmpeg，以及可用的 NVIDIA 驅動。WSL 主機的 `LD_LIBRARY_PATH` 須包含 `/usr/lib/wsl/lib`。目前正式主機已設定為 `h264_nvenc`。編碼器失敗直接回報，不靜默退回 CPU。相同視覺狀態的頁面使用最多兩頁快取；動畫與進度仍由原媒體時間決定，不降低影格率。Linux 影格 pipe 使用 1 MiB 緩衝以減少傳送切換成本。影片工作串行，暫存檔處理後清除；不用下載影片生成模型。來源不足或驗證失敗會保留失敗狀態，不視為通過。

已認可的原節奏樣片可保留實際來源與時間軸作為既有產物；單一樣片不代表任意教材均已驗收。Gemma 分鏡尚未驗證。臨時比較頁及專用產生腳本已移除。


## Podcast 互動與升級邊界

逐字稿與同步圖卡皆使用 Podcast 建立時的 revision resolver 回查原教材。問目前播放段落會暫停播放器、固定該次 locator 並開啟既有 Voice；尚無可靠 alignment 時只允許手動選段，不猜目前 beat。Voice 新對話可指定 exact revision；文字問答的 `context`／錄音的 `X-Studydy-Podcast-Context` 只傳 Podcast ID、episode index、script hash 與文字範圍。後端確認 ownership、revision、範圍及引用，回答只依同 revision canonical claims，context 不成為知識來源。錄音確認、重試與取消沿用原工作生命週期。

`0005_cards_and_media.sql` 的 nullable `voice_turns.context` 保存 locator 與必要來源引用，不複製 script。Podcast 刪除後拒絕使用該 locator 新追問，既有回答依 Voice 保存規則保留；不自動搬移或重製 Podcast、音訊、影片及 manifest。provider、backend 與 frontend 須共同支援相同互動契約。

播放、seek、聽完及 Voice 問答皆不產生 mastery 作答事件。教材學習與程度選擇見[學習與評量](learning.md#起始程度與跨概念診斷)。

30fps 只供 benchmark 輸入使用；正式 bundle 仍要求 1080p60。benchmark 結論及重跑命令見 [測試](testing.md)。原教材圖片／page-region asset、3D、生成影片與模型 executable code 均不在此版本範圍。

## 後端專用 UID

後端與 OCR 子程序使用 `STUDYDY_BACKEND_UID`，與主機開發工作分開計算 NPROC。`STUDYDY_UID/GID` 保留給資料管理者及低執行緒的 bridge、tunnel、下載工具；Podcast user service 亦維持原主機身分，與 backend 透過 HTTP JSON／bytes/base64 交接，不直接共用 artifacts。

首次啟動前，若 `STUDYDY_DATA_DIR` 不存在，先由 `STUDYDY_UID/GID` 對應的主機管理者建立為0700；既有目錄不重設 owner／mode，不依賴 Docker 自動建立 root-owned bind source。init 不重設既有資料根、模型或 PostgreSQL 目錄的權限，只設定 artifacts 根為後端 UID、0700。既有 artifact 子樹須依核定部署步驟另行遷移 owner，不能以 init 代替遞迴遷移。檔案仍保留原0600／0400模式；jerry 必須使用 API 或服務 UID 的受控命令存取，GID1000不授予直接讀寫。

正式版模型維持原 owner、內容及唯讀掛載，部署時只授後端 UID 必要的目錄穿越／讀取 ACL；init 不應清除既有 ACL。模型新增或重下載後須重新核對此讀取權限。不要更動 PostgreSQL 的999身分、共用開發 `.venv` 或主機分析資料。

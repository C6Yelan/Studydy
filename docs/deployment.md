# studydy.net 的 canonical 部署流程

唯一 deployment working directory 是工作區內正式版的 `main` checkout；以下命令均以該目錄為根目錄。
日常 rebuild、Compose 管理、設定與 source-of-truth 都在這個 checkout；build context
是本目錄 `.`，不需要先複製 source 到其他目錄。

從核准的獨立分支部署時，Compose 管理目錄與資料仍固定在 `main`，映像身分記錄實際分支 SHA。
主機 Podcast provider 與每次請求啟動的子程序必須使用同一個已驗證 SHA 的唯讀 runtime bundle，
保存於 `main/data/deployments/<release>/provider-source`，不要直接載入仍在修改的分支 checkout。
bundle 只包含需要的 `backend/src`、`ops` 與 `local_ai/runtime-lock.json`，沿用原 Python／模型環境、
瀏覽器依賴與 `STUDYDY_VIDEO_WORK_DIR`；以 `STUDYDY_PODCAST_JOB_LOG` 明確指向原有工作日誌。
切換前確認沒有進行中的工作，再更新 service 的 `WorkingDirectory`／`ExecStart` 並重啟。
同時記錄 bundle SHA、檔案 hash 與實際啟動路徑，避免容器與主機 provider 運行不同版本。

## Pillow 版本契約

Pillow 固定為 13.0.0，涵蓋後端 test extra、Podcast 影片 requirements 與 OCR 獨立環境。CosyVoice requirements 維持 12.3.0，待 Podcast 新環境實際合成驗收後再升。OCR 使用 Pillow 解析 PyMuPDF 重繪的 PNG；升級須核對相同合成 PDF 的文字與座標，影片另核對實際渲染及字幕排版。

PyMuPDF 維持 1.28.0，renderer 的嚴格版本契約不變。此批不修改 OCR 的 torch／transformers、模型 revision 或 runtime lock；正式 Podcast 的套件更新仍須建立新環境並經驗收，不直接修改正在服務的 venv。

## PostgreSQL 18.6 升級結論

[官方 18.6 發行說明](https://www.postgresql.org/docs/release/18.6/)指出 18.5 未正式發行，18.X 升級不需 dump／restore。依目前 `backend/migrations`：

- `0006_read_indexes.sql` 在 `material_passages` 建立 `material_passages_concepts`／`material_passages_terms` 兩個 GIN。正式升級後須人工檢查該表 `pg_class.reltuples` 是否合理；若異常，安排 `ANALYZE` 修正統計，不能以全套 `REINDEX` 取代此判斷。
- 未宣告 `btree_gist`、`ltree` 或相關 GiST 索引，沒有 migration 層面必做的 REINDEX；若現場另有擴充，需另核對 float NaN／bit 的 btree_gist 索引及超長 ltree 值的 btree 索引。
- 未見自訂 logical-decoding plugin、pgcrypto PGP 或失敗的 `COPY FROM STDIN` 腳本。官方所述 plugin 白名單、受影響密文重加密與 COPY 終止符，僅在現場另有這些用法時評估。

以上只根據程式庫定義，未查產品 DB 的擴充或統計；沒有執行 DB 維護。部署時需另行核定人工操作，不改寫既有 migration。

## 固定部署邊界

- Compose project：`studydy`；公開設定必須同時使用 `compose.yaml`＋`compose.tunnel.yaml`。
  單用 base 檔的初次本機流程，不適用於目前公開站的日常 redeploy。
- cloudflared 僅 edge；frontend 為 edge＋application；backend／model bridge 的網路不變，
  database 保持 internal。公開 route 為 `studydy.net → http://frontend:8080`。
- 維護入口維持 `127.0.0.1:4173`，backend／DB／model／metrics 不發布 host ports。
- backend healthcheck 使用容器內 `/internal/ready`，要求啟動復原完成、所有 worker 執行緒存活及 DB `SELECT 1` 成功；不呼叫模型。此路徑不由 Nginx 代理，也不列入產品 OpenAPI。`/v1/openapi.json` 仍可作 HTTP 存活檢查；readiness 不代表每個生成工作仍有進展或模型可用。
- 持久資料固定在本 checkout 的 `data/`；既有 DB／artifacts／模型掛載不可因建置位置改變。
- 私有 `.env` 留在正式 checkout；`STUDYDY_TUNNEL_TOKEN_FILE` 指定 repo 外的 token 檔絕對路徑，只由 cloudflared 唯讀掛載。
  不讀出、複製或雜湊 token；只檢查 metadata，不把值放進 environment、image 或紀錄。
- HTTPS origin 是 `https://studydy.net`，Secure cookie=true。維護入口不放寬 exact Origin。
- 已確認 Cloudflare zone 上限為 100 MB；單次上傳請求上限為 `94371840` bytes（90 MiB），
  Nginx／API 共用設定，UI 讀取 capabilities。較大的檔案（圖片組、音訊、影片）由瀏覽器分段上傳，
  每段不超過此上限，總大小依各格式上限。既有 artifact 讀取權限不變。
- 已核准公開 Internet 部署；Access 為 optional。沿用目前 Cloudflare 設定，不需為 rebuild
  重新建立 Tunnel、DNS 或憑證。私有 API 保留 `private, no-store`，不強制公共快取。

## Podcast 與教材問答的語音流程

正式 Podcast 使用原參考聲線、V／W 台灣華語指令、整句中英混讀及通用接點／停頓／語速規則。處理直接作用於模型片段及 speaker 邊界，不使用人工指定秒數或 WAV 句界推測；既有音訊保留。

教材問答也共用 Podcast 的整句混讀、台灣口音指令、原 host 聲線、停頓修正與音量處理。舊的中英文分段合成分支已刪除，沒有 legacy 開關；既有音訊保留，新合成與重試使用共用流程。provider 每次音訊請求啟動同一個已部署 runtime bundle 的 `ops/podcast/synthesize.py`；腳本更新需建立新 bundle 並切換 provider，不能改動正在使用的版本。

新語音統一使用 `dynamic-pauses/v1`：短句續講 250ms、一般句尾 550ms、20 字以上長句 850ms、問句與一般換人 650ms、簡短接話前 350ms、換主題 1100ms。依原講稿的文字與數字計數，短句為 6 字以內；縮寫與數值的朗讀展開不改變分類。換段優先，符合一個條件就選定停頓，不疊加；現有片段兩端靜音計入目標。固定停頓機制與臨時試聽設定已移除，`/pause-preview` 及其資產回 404。既有影音不改時間軸，新建節目以新音訊重新對齊字幕及影片。

跨語言教材更新需一起部署後端與 runtime lock。新分析寫入 `localized_meaning_v2`，以完整數量 token 保護短數值；既有 `localized_meaning`／`semantic_meaning` 仍依各自已保存的規則讀取。寫入 v2 後，回復版本必須能讀取 v2；需經驗證的相容映像或前向修復。原有知識結構與來源不搬移、不改 hash；舊分析提示的 checkpoint 不與新的翻譯政策混用。

## Podcast 講稿逐段核對

`ops/podcast/provider.py` 的 `/script` 改為生成後逐段核對（`source-script/v14`），核對提示存於 runtime lock 新增的 `podcast_script_review`（`podcast-script-review/v1`）；已保存的 lock、知識結構與講稿 hash 不改寫，舊 lock 沒有此項仍有效。新增 lock 項目會改變目前 lock 的整份 hash：新工作的 provenance 記錄新 hash，進行中的教材分析、筆記與題組工作沿用各自封存的 lock，不受影響。provider 以新 bundle（含同版 `local_ai/runtime-lock.json`）更新並重啟即生效，不需 migration；backend 的 worker 與錯誤碼不變，舊 backend 可接收 v14 講稿，新 backend 也可接收舊 provider 的 v13 講稿，兩者部署順序不互相依賴，backend 隨下次映像更新帶入新 lock 即可。重啟前照「免費通用搜尋」一節的規則等正在執行的 Podcast／影片／問答工作完成。`/script` 從生成到核對完成都持有 provider 的文字鎖，每集約 60～140 秒（12 段都重寫的最壞情況約 37 次呼叫、4～5 分鐘）；期間教材問答、卡片、主題與研究等 `/semantics` 文字請求會排隊等待（不逾時）。工作日誌 `script_review` 記實際呼叫次數。bundle 內程式與 lock 不一致時，工作日誌記 `PODCAST_REVIEW_CONTRACT_INVALID`，backend 顯示為 `PODCAST_PROVIDER_FAILED`。

## 媒體來源（圖片、音訊、影片、YouTube）

- 後端新增 Python 依賴 `av==19.0.1`（PyAV，wheel 內含 FFmpeg，其中包括 GPL 授權的 libx264／libx265 編碼器；本站以網站服務提供，不散布映像或執行檔，轉檔只用解碼與 AAC／Opus 編碼）與 `yt-dlp==2026.10.21`（Unlicense），記在 `backend/pyproject.toml`／`uv.lock`。沒有新系統套件，輕量更新的 `uv sync` 即會安裝，不需完整重建 OCR 映像；部署後在容器內核對 `import av, yt_dlp` 與版本。
- Migration `0016_media_sources.sql` 放寬 `artifacts` 的原檔型別（ZIP 圖片組、MP3／M4A／WAV、YouTube 參照）及音訊原檔 300 MiB 上限；只改 check constraint，不搬資料。
- 主機 provider 新增 `/media-transcribe` 與 `ops/podcast/transcribe_media.py`（顯示卡辨識，沿用 STT 環境與 CUDA `LD_LIBRARY_PATH`）。先以新 bundle 更新並重啟 provider，再更新 backend；舊 provider 收到請求會回 404，來源標為 `STT_FAILED`，可在更新後重試。
- 分段上傳與轉換暫存在 `data/artifacts/.media-work/`；單支影片暫存最多 2 GiB，部署主機需保留對應空間。中斷超過一天的分段上傳與轉換暫存（含程序被終止時留下的原檔副本與 YouTube 音軌）由維護 worker 清除。
- YouTube 改版造成讀取失敗時，更新 `yt-dlp` 版本並重建後端；失敗的來源可重試。

## 核准 source 與直接建置

一般 Git 整合仍為 `feature/* → dev → main`。學生專題需要外網、手機或語音實測時，
可從使用者當次核准的 branch／完整 SHA 直接部署到同一個 studydy.net；不另建 dev domain。
部署 SHA 以本次實際建置的 commit 為準，不假設一定等於最新 main，也不以 branch 名取代 SHA。

進入已核准的正式 checkout 根目錄，取得 rebuild 授權後執行：

```bash
test -z "$(git status --porcelain=v1)"
export STUDYDY_RELEASE_SHA="$(git rev-parse HEAD)"
git branch --show-current
docker compose --env-file .env -p studydy -f compose.yaml -f compose.tunnel.yaml config --quiet
```

先確認該 SHA 已獲准、相關測試通過、migration 與資料 mount 符合本次範圍，保存目前
container image IDs／私有設定與 rollback image tags。需要資料備份時依
[安裝文件](getting-started.md#資料備份與還原)處理；不把 token 檔納入 source 或設定備份。
檢查 build 目錄沒有未追蹤的產品 source；既有 `.dockerignore` 排除 `.env`、資料、
`docs_local`、venv、node_modules、測試輸出與私鑰，保持秘密不進 build context。

直接從 checkout 建置前後端，不重新建置 DB、model bridge 或 connector：

```bash
docker compose --env-file .env -p studydy -f compose.yaml -f compose.tunnel.yaml build frontend backend
```

Compose build 本身不自動加入 Git revision label。若要在 image 中保留
`org.opencontainers.image.revision`，用下面兩條等價命令**取代**上面的 build；Dockerfile、
context、target 與 Compose 使用的 tags 相同，仍直接使用目前 checkout：

```bash
docker build --label "org.opencontainers.image.revision=$STUDYDY_RELEASE_SHA" \
  --target ocr -f ops/docker/backend.Dockerfile -t studydy-backend-ocr:latest .
docker build --label "org.opencontainers.image.revision=$STUDYDY_RELEASE_SHA" \
  -f ops/docker/frontend.Dockerfile -t studydy-frontend:latest .
```

## 系統與 OCR 未變更的輕量更新

磁碟容量有限時，先將目前已驗證的 backend／frontend image ID 分別加上 `studydy-backend-runtime:local` 與 `studydy-frontend-runtime:local` 標籤，再使用 `compose.app-update.yaml` 覆寫建置設定。這些標籤不複製映像。後端依 `backend/uv.lock` 同步應用程式 Python 套件，再替換 source、migrations 與 runtime lock，不重新安裝或複製 OCR 環境；只有語意分批／提示設定改動時可沿用此方式。系統套件、Python runtime、local_ai 套件或 OCR 模型／設定改動仍需完整建置。

前端先在 checkout 使用既有 node_modules 執行 `npm test` 與 build，將輸出放入 `.studydy-runtime/frontend-static/site/`，同目錄放入 `ops/docker/nginx.conf` 及 `ops/docker/frontend-static.Dockerfile`（命名為 Dockerfile）。建置時保留 `compose.yaml`、`compose.tunnel.yaml`，最後加上 `compose.app-update.yaml`。部署使用已建好的映像及 `--no-build`，不要意外觸發完整 OCR 建置。

建置前後檢查 Linux 與宿主磁碟可用容量，保留至少 20 GiB；異常成長時停止並檢查，不反覆重建。保存來源 diff、映像 ID 與 migration 前的本機 DB 備份。工作樹有經授權的未提交變更時以實際 source snapshot／diff 記錄，不能宣稱部署內容等於 HEAD。

## 記錄 identity、切換與回復

不論採哪個 build 命令，都要保存「核准 SHA＋branch＋實際 image ID／可用 digest」配對。
下例僅讀取 Git 與 image metadata，不展開 Compose secrets；紀錄放在持久資料目錄而非 temporary source：

```bash
python3 - <<'PY'
import datetime, json, os, pathlib, subprocess
sha = os.environ['STUDYDY_RELEASE_SHA']
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == sha
assert not subprocess.check_output(['git', 'status', '--porcelain=v1']).strip()
record = {'source_sha': sha, 'branch': subprocess.check_output(
    ['git', 'branch', '--show-current'], text=True).strip(), 'images': {}}
for service, tag in [('frontend', 'studydy-frontend:latest'), ('backend', 'studydy-backend-ocr:latest')]:
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', tag]))[0]
    record['images'][service] = {'id': image['Id'], 'digests': image.get('RepoDigests', [])}
    label = (image['Config'].get('Labels') or {}).get('org.opencontainers.image.revision')
    record['images'][service]['observed_image_revision_label'] = label
path = pathlib.Path('data/deployments')
path.mkdir(mode=0o700, parents=True, exist_ok=True)
stamp = datetime.datetime.now(datetime.UTC).strftime('%Y%m%dT%H%M%S%fZ')
with (path / f'{stamp}-{sha}.json').open('x') as stream:
    json.dump(record, stream, indent=2)
PY
```

基底映像可能帶有上游 revision label；不能把它當成 Studydy 的 SHA。只有使用上面的
`--label` 建置方式時，才額外要求該 label 等於核准 SHA；所有方式都以本次 build 的
`source_sha`＋實際 image ID 配對為準。純本機 build 沒有 registry digest 時保留空值，不偽造。
完成本次必要的 image 檢查，
確認資料備份與 rollback 可用後，才在核准切換時段執行：

```bash
docker compose --env-file .env -p studydy -f compose.yaml -f compose.tunnel.yaml \
  up -d --no-deps --no-build --wait backend frontend
docker inspect --format '{{.Name}} {{.Image}}' studydy-frontend-1 studydy-backend-1
```

把實際 container 的 `.Image` 與剛才的 identity 紀錄逐一核對，再驗證 localhost、公開 HTTPS、
登入／Origin／cookie、資料 ownership、上傳限制與 private cache。記錄驗收結果和 deployed SHA；
更新 Git 分支或純文件 commit 本身不會更新 running image 的 SHA。正常前後端 redeploy
不需重啟 cloudflared；若另有核准的 connector 更新，仍從同一 checkout 使用兩個 Compose 檔及
`tunnel` profile，只管理 cloudflared。

失敗時先隔離有問題的公開入口，保留 localhost 維護，再以記錄的舊 images/config 回復。
不清除 DB／artifact／volume、不將 DNS 指向主機、不公開 backend，也不為除錯放寬驗證。
既有部署證據與 rollback backups 可保留；不要把歷史 `/tmp` helper 當作日常部署入口。

## 必要驗證工具

`ops/tests/compose_boundary.py` 使用明確的 `STUDYDY_TEST_ENV_FILE`，只列安全摘要。
`ops/tests/nginx_boundary.py` 使用指定測試 images、隔離 echo upstream 與 loopback 4183。
Backend runtime tests 使用 disposable DB；不以產品資料跑測試，不因部署檢查呼叫 Pod／Gemma。
純文件整理只檢查命令／路徑與 Compose metadata，不需重建線上 images。

## 已撤除的試聽與實驗入口

`/podcast-voice-comparison`、`/voice-compare-*`、`/pause-preview`、`/animation-poc-review`、`/podcast-visual-test` 及其資產應維持 404，不回到 SPA，也不恢復臨時 Compose 掛載。既有 Podcast、教材、正式參考音與共用環境保留；origin 檔案刪除不代表 CDN 快取或外部歷史副本已清空，外部副本須另行核對。

## 免費通用搜尋

`research-search` 使用鎖定 digest 的 SearXNG 映像與 `ops/search/settings.yml`，不發布主機 port，也不需要付費 API key。初次接入先以原本的 HTTPS Compose 組合執行 `up -d --no-deps --wait research-search`，再更新 backend／frontend。搜尋用的內部簽署密鑰在啟動時產生，不寫入版本庫。

搜尋關鍵詞提示位於主機的 `ops/podcast/provider.py`；更新時要保留已執行中的 Podcast／影片／問答工作，確認工作完成的安全空檔後才重啟 provider 與 backend。不能把取消整份 Podcast 當作部署前的例行清理。搜尋服務失敗會保留其他搜尋結果及部分失敗狀態。

## 0006 按需教材讀取索引

`0006_read_indexes.sql` 建立導覽／學習規則及單筆 Evidence 定位投影，並包含 `card_source_concepts` 的完整概念內容。原始 PDF、canonical document、revision 與作答事件不變；新發布教材在同一交易內建立索引，缺少索引的既有教材須先補建。

保存必要備份與實際映像身分後，使用新建的 backend image 執行：

```bash
docker compose --env-file .env -p studydy -f compose.yaml -f compose.tunnel.yaml \
  -f compose.app-update.yaml run --rm --no-deps backend prepare-read-indexes
```

此命令先套用 migration，再逐版本驗證並補建缺少的索引；可重跑、不呼叫模型。確認沒有進行中的發布工作，更新 backend，再重跑補建以涵蓋切換期間完成的舊版工作，最後更新 frontend；不要在一般 GET 交易內補建大型索引。

此命令也會修復舊概念索引遺漏的關係專屬 Evidence：保留既有 Claim 節錄，只從 canonical Evidence 補回關係缺少的原文、頁碼與來源位置，原地更新衍生 payload／hash。教材、revision、原檔及學習紀錄不重寫；完成後重跑回報 0。缺少概念或學習索引的正常讀取回報 `KNOWLEDGE_STRUCTURE_UNAVAILABLE`，不隱式寫入。

完整教材／resume API 仍保留，新版使用 outline、concepts、map-progress 與 workspace 讀取。API 為 `private, no-store`，每次核對 owner、發布版本、runtime／來源綁定與投影雜湊；瀏覽器記憶體快取隨帳號 client 清除，不存入 localStorage。

## 0004 需求確認與研究原檔

`0004_research.sql` 包含綁定教材與 revision 的補充規劃、研究刪除標記及本機研究原檔快照。新研究一律先確認範圍，沒有直接建立研究的舊入口；刪除研究會隱藏紀錄並撤銷尚未送出工作的 lease，保留教材、原始檔案及已送出的分析。

第一次閱讀的原檔保存供後續選取；快照清理只作用於研究準備表，已匯入教材的 Artifact 仍依既有生命週期管理。前後端須使用相容的需求確認契約，驗收限制見 [測試文件](testing.md#需求確認與論文語意搜尋)。

## 論文取得與證據判讀

研究流程支援首次閱讀的公開替代版本、PDF 優先及 HTML 正文補救、文字快照，以及保留有限證據的閱讀／涵蓋規則。舊工作續接時重核不同政策的涵蓋快取，保留原檔與已讀引用；不得為套用新政策重抓或丟棄已讀來源。

文字快照須由支援該格式的後端讀取，不能直接切回只識別 PDF 快照的舊程式或覆蓋產品 DB。驗證結果須區分來源取得、引用定位與內容品質，不能以其中一項通過取代其他驗證。

## 0008 個人顯示名稱

`0008_learner_display_name.sql` 在 `learners` 新增 nullable `display_name` 與 CHECK，不回填、不改寫既有資料。baseline 仍只鎖定 0001–0007，後續版本由啟動流程套用；回復後端時須保留對已套用 schema 的支援。

## 0009 概念卡學習標記

`0009_card_reviews.sql` 建立 `card_review_events`、`card_review_states`，對卡組外鍵為 ON DELETE CASCADE。套用前在獨立 DB 副本驗證並核對原業務資料不變；還原驗證副本時等待 PostgreSQL 初始化完成，使用 `--exit-on-error` 確認還原成功。

概念卡從間隔複習改成學習標記時，經使用者明確同意以單次例外直接改寫已套用的 0009（移除 `streak`、`interval_days`、`due_at`、`rule_version` 欄位與到期索引，改為 `(learner_id, last_rating)` 索引），沒有另加 migration。之後使用者再次明確同意改寫 0009，把標記從三種縮成兩種：兩張表的 CHECK 只接受 `forgot`／`remembered`，原有的 `fuzzy`（不太熟）資料改成 `forgot`（不熟）。這兩次都是使用者逐次授權的例外，不適用於其他 migration。

已套用舊版 0009 的資料庫需在新後端啟動前手動轉換，否則啟動時的 checksum 核對會失敗：先備份這兩張表，停止 backend，在同一交易內刪除上述欄位與 `card_review_states_due` 索引、建立 `card_review_states_learner` 索引，把兩張表的 `fuzzy` 改成 `forgot` 並換成只接受兩種值的 CHECK，並把 `schema_migrations` 版本 9 的 `sql_sha256` 改成新檔的 SHA-256，再啟動新後端。標記與事件保留，舊的 forgot／fuzzy 成為不熟、remembered 成為熟悉。正式 DB 已完成轉換；轉換前先在正式 DB dump 的一次性副本演練，確認轉換後兩張表的結構與新 0009 從頭建立的結果相同、migration 執行器核對通過且沒有待套用版本。新後端不能與舊結構並存，退回需同時還原兩張表與舊後端映像。

## 0010 Podcast 收聽進度

`0010_podcast_listening.sql` 新增 `podcast_episode_progress`、`podcast_listening_days`，對 Podcast 外鍵為 ON DELETE CASCADE，不修改既有資料。前後端需共同支援 `learning-activity/v2`；舊前端讀到 v2 時不顯示本週節奏。

## 0011 教材新版承接

`0011_version_moves.sql` 新增 `material_version_moves`、`card_set_moves`、`study_session_moves`，保存學生確認、重送結果、卡組與概念對應及學習完成承接。對教材、卡組與學習的外鍵為 ON DELETE CASCADE，不改寫既有資料；舊前端不顯示新版提示，但已承接資料仍可讀。

## 學習活動 v3 與成就規則

後端使用 `learning-activity/v3`（經驗值 `xp-rules/v3`、徽章 `badge-rules/v6`）並提供 `/v1/learning-activity/rhythm`；前端只接受這組版本，前後端必須一起上線，不可只更新其中一邊（任一邊是舊版時，頁首等級、成就與個人檔案的學習進度都會因版本不符而讀不到）。不需 migration，退回時前後端一起退回。

## 0013 登入裝置清單

`0013_session_devices.sql` 只在 `learner_sessions` 新增 nullable `device_label`（1–64 字元 CHECK）與 `last_used_at`，不回填、不改寫既有資料；新後端啟動（`serve`／`migrate`）時由 `run_migrations` 自動套用。套用前同 0009–0011 在獨立 DB 副本驗證並核對原業務資料不變。後端使用 `account-sessions/v2`（裝置清單，取代登入數），並提供 `DELETE /v1/account/sessions/{session_id}`；前端只接受 v2，前後端必須一起上線（只更新任一邊時，個人檔案讀不到登入裝置，修改密碼成功後也會顯示錯誤）。套用後需使用識別 0013 的後端，採前向修正；退回時前後端一起退回。既有登入沒有裝置標籤，顯示「未知裝置」，最後使用時間在下一次請求後開始記錄。

## 0014 起始程度與跨概念診斷

`0014_starting_level.sql` 新增教材的 nullable `starting_level` 與學習開始請求回執，不推測或回填學生程度。題組新增 `material_diagnostic` 種類，調整跨概念成員範圍外鍵，保留原單概念／補強與私密答案的範圍保護；既有題組及答案不改寫。部署前備份產品 DB，並在隔離資料庫驗證 0013 升級至 0014 保留原資料。

前後端須一起更新，以支援程度選擇、空的整組概念目標及每題自己的概念。0014 一經套用，舊版 migration runner 會因不識別新帳本版本而拒絕啟動；舊題組讀取程式也不識別跨概念資料。回復須採支援 0014 的修正版或前向修正，不能只重啟舊映像。若需還原部署前 DB，必須先處理切換後的新資料，不自動覆蓋或丟棄新題組與作答。

## 0015 目標日期與分天關卡

`0015_learning_goals.sql` 新增目標、排期／當日計量基準及版本更新提示的已讀資訊，對擁有者與教材有外鍵保護，不改寫既有題組、答案或完成規則。套用前備份產品 DB，並在隔離環境核對升級後原資料保留；同批備份只在新版部署驗證完成後取代前一份，不刪除其他既有備份。

前後端一起更新，才能顯示教材目標、首頁關卡與島嶼摘要。套用 0015 後，回復版本必須識別新 migration 與當日基準寫入規則；採相容修正版或前向修復，不直接重啟舊版，也不自動還原 DB 丟棄新的設定或作答。不需補跑模型或建立固定未來日程。

## 0017 文字筆記與個人註記

`0017_material_notes.sql` 新增筆記生成／快取及個人註記兩張表，不修改既有教材、題組或答案。套用前備份產品 DB；隔離環境由 0016 升級並逐表核對既有資料不變。新後端須與支援筆記分頁的前端一起部署。已套用後只能使用識別 0017 的相容版本或前向修復，不直接啟動舊 migration runner，也不還原 DB 丟棄新註記。

本批筆記使用目前正式 Luna（gpt-5.6-luna）；雲端 Pod／Gemma 切換不屬本批，沒有新增切換抽象。筆記請求使用 runtime lock 的 `material_notes`／`material-notes/v12` 契約（v12 改為結構化格式：有層級的小節、巢狀條列、多表格、LaTeX 公式與醒目區塊，寫作與核對提示已改，挑選提示不變；v11 取消重點整理的 A4 一頁限制）；包含寫作、摘要選點及逐段內容核對提示；原教材與題組保存的舊 lock／hash 不改寫。未完成筆記重試時若契約已不同，清除未發布的分區暫存並換用目前 lock，不接續舊契約批次。沿既有文字 provider，不修改 Podcast provider 程式或重啟其服務。

部署後由維護程序呼叫 `runtime.material_notes.queue_existing(owner=<已授權 learner UUID>)`，僅為本人目前版本缺少的重點整理排工作；重跑不重新生成已有結果，也不建立完整筆記。回報教材份數與實際 provider 呼叫次數，不能把工作嘗試數當成模型請求數。程式失敗或容量不足時如實保留 failed，學生可手動重試。`queue_existing` 會跳過已有紀錄（包含 failed 與舊契約已發布）的教材；v11 部署不需 migration，舊契約已發布的重點整理保留到重新生成為止；只重做指定的列（failed 或要換新契約的已發布列）時，逐份對該列呼叫 `runtime.material_notes.generate(owner, material_id, revision, "summary", expected_generation=<該列 generation>)`（與 API 重新生成相同），契約不同時會換用目前 lock，已發布筆記與註記保留。

### 結構化筆記（material-notes/v12）

不需 migration：`material_notes.paragraphs` 與註記快照仍是 jsonb，新舊段落格式並存；API 的 `schema` 改為 `material-notes/v2` 並新增 `outline`，前後端須一起部署（舊前端不認得 v2）。後端新增 `matplotlib==3.11.2`（連帶 contourpy、cycler、kiwisolver、pyparsing，numpy／Pillow 由 test extra 轉為正式依賴，版本不變），記在 `backend/pyproject.toml`／`uv.lock`；沒有新系統套件，輕量更新的 `uv sync` 即會安裝。公式排版使用映像內既有的 `fonts-noto-cjk`，matplotlib 第一次排版時在 `$HOME/.config/matplotlib`（backend 的 HOME 為 /tmp tmpfs）建立字型快取，約需數秒；部署後在容器內核對 `import matplotlib` 版本並匯出一份含公式的筆記。前端新增 `katex@0.18.11`（MIT），公式元件與 KaTeX 字型拆成獨立 chunk，只在筆記有公式時載入。runtime lock 的 `material_notes` 改為 v12；未完成的 v11 工作重試時依既有規則清除未發布批次並換用 v12。已發布的 v11 筆記保留可讀與匯出，重新生成後才換成新格式。

## 0018 筆記生成診斷摘要碼

`0018_material_note_diagnostics.sql` 只在 `material_notes` 新增可為 NULL 的 `generation_diagnostics jsonb`，既有列保持 NULL，不改其他欄位或資料表；隔離環境由 0017 升級並逐表核對既有列不變。套用前備份產品 DB。只需更新後端（不需前端；可用輕量更新，不需完整重建 OCR 映像，也不改 runtime lock）；新後端讀寫此欄位，必須先套用 0018 才能啟動。已套用後回復版本須識別 0018（舊程式不讀此欄位，可相容），不還原 DB。欄位內容與日誌 `NOTE_GENERATION_ADJUSTED`／`NOTE_GENERATION_FAILED` 的 `diagnostics=` 相同，只含錯誤碼、事件、段落位置、重點編號、核對理由 SHA-256 前 12 碼與頁高比例；查詢時直接讀 DB，API 不回傳。

## 後端身分與既有資料

`STUDYDY_BACKEND_UID` 預設20001，GID沿用1000；它只控制 backend（含OCR）及其 runtime tmpfs，image 的 backend／OCR／test 預設身分也一致。完整建置時 Compose 以同名建置參數在 backend 階段建立 `studydy-backend` passwd 項目（HOME=/tmp tmpfs）；torch／transformers 以 `getpass`、`~` 決定身分與 cache，缺少此項目時 OCR 子程序會 CHILD_EXITED。改 UID 須完整重建 backend 映像，輕量更新沿用 runtime 映像內的項目。init 仍由0執行，bridge、cloudflared、download-ocr與主機Podcast維持原1000管理身分。SSH key與tunnel token不隨後端UID搬移或放寬權限。

既有artifacts需由核准部署程序先遷移owner，保留0700目錄及原0600／0400檔案契約。不得遞迴chown整個data；data根、data/podcast、PostgreSQL、模型內容及jerry分析資料保持原狀。模型僅授新後端UID只讀ACL。部署／回滾前應在停止該版backend寫入後重新盤點範圍，具體路徑、數量、操作順序與回滾命令由本次部署狀態檔提供，不在產品初始化時自動遷移。



## 學習資料匯出

exports 不需 migration。後端新增 `genanki`、`python-docx` 與 `fonttools`，依 `uv.lock` 安裝；沿用現有 PyMuPDF、PyAV 與映像內 `fonts-noto-cjk`。PDF 從 Noto 字型集合選取繁體中文標準／粗體並嵌入，不依賴瀏覽器列印；MP3 使用 PyAV 的 libmp3lame，不另起模型或轉檔服務。

輕量後端更新須以 `docker inspect studydy-backend-1` 當下映像為基底，保留 UID 20001 的 passwd 項目及原 OCR 環境。部署前核對中文字型標準／粗體、libmp3lame 與新增依賴可用。前端增加 `html-to-image`，完整渲染既有島嶼元件後產生 PNG；後端核對 owner、版本與完整概念集合後打包；匯出內容不寫入一般 log。

匯出以非同步 semaphore 同時最多處理兩份產檔；名額在 endpoint 函式結束時釋放，不延長到回應串流。既有 MP4 直接串流，不取得產檔名額。等待名額不佔用工作執行緒。卡組透過建立暫存、每批至多兩張 PNG 的 PUT、最後下載三個步驟傳輸，不把整組 base64 放進同一個請求。卡面、Anki SQLite 與輸出檔放在 artifact root 的私有 `.export-work/`，不使用只有 1 GiB 的 `/tmp`，不新增 DB migration。完成／取消／組檔失敗會清除該工作；維護 worker 清理超過 30 分鐘未更新且未鎖定的暫存。匯出與清理共用檔案鎖，下載尚未結束時不會被刪除。


卡組每份接受的 PNG 合計上限 512 MiB（按實際暫存 PNG 大小計算），每個概念最多 16 張、整份最多 2000 張；每個帳號最多同時保留兩份卡組匯出，包含正在下載、尚未清理的工作。建立工作以檔案鎖原子檢查帳號名額，暫存目錄名稱綁定 owner；超限分別回 `EXPORT_SIZE_LIMIT`（413）、`EXPORT_PAGE_LIMIT`（413）與 `EXPORT_ACTIVE_LIMIT`（409），前端顯示對應限制。打包成品另占磁碟空間，與 PNG／Anki SQLite 在下載結束後一起清理；卡組檔案 lease 保持到串流結束，不隨產檔名額提前釋放。同一工作正在上傳或下載時，重複操作回 `EXPORT_IN_PROGRESS`（409），不占用產檔名額等待檔案鎖。

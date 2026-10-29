# studydy.net 的 canonical 部署流程

## 指定教材的一次性重分析

POST-03 的維護指令只處理數學教材 `4003f055-12f1-462e-83a1-015daffdfdce`。先依本文件完成合併、部署與 migration 0021，再從正式 `main` checkout 執行：

```bash
ops/reanalyze-demo-material --enqueue
ops/reanalyze-demo-material
```

指令只呼叫已部署 backend 容器中的程式；預設查狀態，不建立工作。`--enqueue` 使用固定冪等鍵，重複執行仍回同一 run，不能建立第二次分析或重設預算。維護工作只允許目前已授權的 Luna provider，保留原來源、舊版、學習紀錄與新版承接流程；不修改原 runtime binding。原工作失敗或取消也不透過一般 retry 另建一筆維護工作。

分析、審查、合併、新版章節與首次自動摘要共用 300 次額度，每次外送前在 DB 原子保留。`budget.reserved_calls` 是保守的已保留請求上限，連線失敗與不確定是否受理的請求不退額，不等於外部計費明細。重啟與首次摘要重試沿用同一計數；耗盡回報 `MATERIAL_MODEL_BUDGET_EXHAUSTED`，不得改上限或重建 run。首次摘要完成後的手動重生、完整筆記與其他教材不使用本次額度。

`0033_maintenance_budget_range.sql` 把 DB 約束 `demo_reanalysis_budget` 改為每個維護 run 自訂上限（`limit` 1～2000，`reserved_calls` 不超過該 run 的 `limit`）；本入口建立的工作仍是 300，已存在的 run 不變。其他經使用者授權的一次性維護重分析可在建立 run 時設定較高上限，仍在外送前原子保留、不得超支。

使用者另行授權原失敗工作 `257bef38-6c08-4baf-aedc-641d53a26795` 再恢復一次。此修正合併部署後才可執行：

```bash
ops/reanalyze-demo-material --resume-once
```

此入口只接受該教材的指定 run 與原冪等鍵，原服務錯誤失敗、未取消、head 未變且沒有其他進行中工作時，才原子標記這次恢復已使用並把同一 run 排回佇列。原 `reserved_calls`（目前 11）及 `limit=300` 不變，run／來源／runtime hash／建立時間不變；重複執行拒絕，再失敗也不自動重排。它不開放一般 retry，不可與 `--enqueue` 同時使用。worker 重新建立顯示進度並沿既有策略重用同 run 的可用 Evidence、子圖及審查快取；部分舊格式分析可能再次送模型，仍計入同一額度，不保證從自身 checkpoint 的階段直接接續。

查詢同時列出分析 `state`、`chapters` 與 `summary`；分析成功不等於衍生工作已完成。完成後仍須確認「正比例」、其他概念及舊版學習承接；`needs_review` 不視為內容已驗收。此指令不負責合併、部署或重啟 provider。

## 正式部署入口

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

## Podcast 與Studydy AI BOT的語音流程

正式 Podcast 使用原參考聲線、V／W 台灣華語指令、整句中英混讀及通用接點／停頓／語速規則。處理直接作用於模型片段及 speaker 邊界，不使用人工指定秒數或 WAV 句界推測；既有音訊保留。

Studydy AI BOT也共用 Podcast 的整句混讀、台灣口音指令、原 host 聲線、停頓修正與音量處理。舊的中英文分段合成分支已刪除，沒有 legacy 開關；既有音訊保留，新合成與重試使用共用流程。provider 每次音訊請求啟動同一個已部署 runtime bundle 的 `ops/podcast/synthesize.py`；腳本更新需建立新 bundle 並切換 provider，不能改動正在使用的版本。

新語音統一使用 `dynamic-pauses/v1`：短句續講 250ms、一般句尾 550ms、20 字以上長句 850ms、問句與一般換人 650ms、簡短接話前 350ms、換主題 1100ms。依原講稿的文字與數字計數，短句為 6 字以內；縮寫與數值的朗讀展開不改變分類。換段優先，符合一個條件就選定停頓，不疊加；現有片段兩端靜音計入目標。固定停頓機制與臨時試聽設定已移除，`/pause-preview` 及其資產回 404。既有影音不改時間軸，新建節目以新音訊重新對齊字幕及影片。

跨語言教材更新需一起部署後端與 runtime lock。一般來源新分析寫入 `localized_meaning_v2`，以完整數量 token 保護短數值；既有 `localized_meaning`／`semantic_meaning` 仍依各自已保存的規則讀取。寫入 v2 後，回復版本必須能讀取 v2；需經驗證的相容映像或前向修復。原有知識結構與來源不搬移、不改 hash；舊分析提示的 checkpoint 不與新的翻譯政策混用。

## Podcast 講稿逐段核對

`ops/podcast/provider.py` 的 `/script` 改為生成後逐段核對（`source-script/v17`），核對提示存於 runtime lock 的 `podcast_script_review`（`podcast-script-review/v2`）；已保存的 lock、知識結構與講稿 hash 不改寫，舊 lock 沒有此項仍有效。新增 lock 項目會改變目前 lock 的整份 hash：新工作的 provenance 記錄新 hash，進行中的教材分析、筆記與題組工作沿用各自封存的 lock，不受影響。provider 以新 bundle（含同版 `local_ai/runtime-lock.json`）更新並重啟即生效，不需 migration；backend 的 worker 與錯誤碼不變，舊講稿保持可讀；本次繁中更新須先上線 provider bundle，再部署帶有新 lock 的 backend。重啟前由協調者建立 HOLD_LUNA，照「免費通用搜尋」一節的規則等正在執行的 Podcast／影片／問答工作完成。`/script` 從生成到核對完成都持有 provider 的文字鎖，每集約 60～140 秒（12 段都重寫的最壞情況約 37 次呼叫、4～5 分鐘）；期間Studydy AI BOT、卡片、主題與研究等 `/semantics` 文字請求會排隊等待（不逾時）。工作日誌 `script_review` 記實際呼叫次數。bundle 內程式與 lock 不一致時，工作日誌記 `PODCAST_REVIEW_CONTRACT_INVALID`，backend 顯示為 `PODCAST_PROVIDER_FAILED`。

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

在正式版 `main` checkout、乾淨的 `dev` 執行固定指令：

```bash
ops/deploy --sha "$(git rev-parse HEAD)" --dry-run
ops/deploy --sha "$(git rev-parse HEAD)" --storage-state /tmp/studydy-auth/state.json
```

`--storage-state` 是既有正式站登入瀏覽器匯出的 Playwright storage state；放在本機私有暫存目錄（0700／檔案0600），不可提交、輸出內容或外送。指令使用現有 `frontend/node_modules/@playwright/test` 與已安裝的 Chromium，確認公開首頁、`/profile` 及認證 API `/v1/session`，不建立帳號或教材。執行完由提供憑證者清除暫存。前端需已有與 lock 相符的 node_modules，沿用目前建置環境。

指令核對 `dev`／完整 SHA／乾淨工作樹，以 `.studydy-runtime/thetawave-batch/DEPLOY.lock` 與既有部署程序互斥，檢查 Linux 與 E 槽至少保留20 GiB。從正式容器的實際 image ID 建立本次專用底圖與回復標籤，後端同步 `backend/uv.lock` 後替換 source、migrations、runtime lock；前端重建靜態檔並固定目錄0755／檔案0644。建置後驗證 UID 20001 passwd 與 Nginx 可讀性；不依賴歷史映像名稱。

等待持久工作及有效租約清空後，停止後端寫入、再次確認佇列為空，再備份 DB、建置、套用 migration 與切換。這段期間站台 API 暫停；不取消任何工作。`--wait-timeout` 預設1800秒，逾時就停止。dump 的077 umask只作用於備份，不影響前端建置。部署一律使用已建映像及 `--no-build`，不管理 cloudflared／DNS／競賽版或主機 provider。

已明確接受中斷風險時可加 `--no-queue-wait`，跳過等待及停機後佇列檢查；進行中工作可能失敗，需由使用者重試，不保證租約到期後自動完成。release 的 `queue.preflight` 保存部署前各表工作數及工作表名、id、狀態與 `account`（demo／try／c6yelan，其餘為 null）；帳號對照讀本機私有 `~/.config/studydy-deploy/accounts.json`，紀錄不含 email、密碼或 cookie。既有工作恢復政策不變，備份與部署驗證仍執行。

公開檢查前以 `ip monitor link address route` 等待網路事件連續靜默3秒（總期限30秒），涵蓋Docker建立介面後延遲的IPv6位址發布與本機實測約2秒的Chromium網路通知。等待結果寫入release紀錄；需有主機既有的 `ip` 指令。期限耗盡或監看失敗直接失敗；穩定後仍只執行原本一次完整檢查，不略過公開HTTPS、HTTP200或登入要求，也不重試其他錯誤。回復後檢查沿用同一條件。

`--dry-run` 只做前置檢查、讀正式映像、等佇列及建置／映像驗證，不備份、不停止或切換正式容器。正式執行記錄於 `data/deployments/<名稱>-<UTC>/release.json`，包含映像ID、前版映像、migration前後版號、備份位置與檢查結果；同目錄有 `compose.update.yaml`／`compose.rollback.yaml`。`--name` 預設 `app-update`。

失敗立即停止並回報原因。本次沒有套用 migration 時，切換／停寫後的失敗會自動以回復檔啟動前版並重查正式站；本次有套用（包含部分提交）或帳本結果無法確認時，不啟動舊映像、不還原DB，記錄備份位置，交人工前向修復。回復本身失敗也明確回報。`--min-free-gib` 可提高容量門檻以驗證拒絕；`--check-url` 是部署後額外HTTP檢查，可用必定失敗的本機位址驗證回復，不會略過正常網站檢查。

系統套件、Python runtime、local_ai 套件或 OCR 模型／設定改動仍需完整建置，沿用前後章節的人工建置、identity、切換與回復流程；輕量部署不再另寫手動 deploy.sh。SHA授權、版本相容性與變更所需測試仍由部署前審查確認。

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

## 學習活動 v4 與成就規則

後端使用 `learning-activity/v4`（經驗值 `xp-rules/v5`、徽章 `badge-rules/v8`）並提供 `/v1/learning-activity/rhythm`；前端只接受這組版本，前後端必須一起上線，不可只更新其中一邊（任一邊是舊版時，頁首等級、成就與個人檔案的學習進度都會因版本不符而讀不到）。現行規則需要 0019 與 0029（見下節）；退回時前後端一起退回。

## 0013 登入裝置清單

`0013_session_devices.sql` 只在 `learner_sessions` 新增 nullable `device_label`（1–64 字元 CHECK）與 `last_used_at`，不回填、不改寫既有資料；新後端啟動（`serve`／`migrate`）時由 `run_migrations` 自動套用。套用前同 0009–0011 在獨立 DB 副本驗證並核對原業務資料不變。後端使用 `account-sessions/v2`（裝置清單，取代登入數），並提供 `DELETE /v1/account/sessions/{session_id}`；前端只接受 v2，前後端必須一起上線（只更新任一邊時，個人檔案讀不到登入裝置，修改密碼成功後也會顯示錯誤）。套用後需使用識別 0013 的後端，採前向修正；退回時前後端一起退回。既有登入沒有裝置標籤，顯示「未知裝置」，最後使用時間在下一次請求後開始記錄。

## 0029 單概念學習資料清理

0029 將題組恢復為每組必屬一個概念，刪除舊跨概念題組、題目、答案與相關開始收據，重編剩餘作答事件。另清理相依的三枚徽章、含舊獎勵的教材日快照，以及受影響帳號需要重算的作答／進度／XP 徽章。無關卡片、Podcast、筆記、匯出等紀錄保留。

只對有舊跨概念題組的教材重計目標基準、清除歷史關卡與達標紀錄；移版標記只保留有正常作答或通過題組依據的完成。正常題組與私密答案保護不變。受影響經驗值、等級、完成量與目標達成可能下降；相依徽章重算後可能再次慶祝。

前後端須同批更新：學習開始回應、進度、地圖進度、工作區、工作區進度、題組與題組列表都升為 v2；開始請求仍為 v1。學習活動使用 v4、XP 規則 v5、徽章規則 v8。部署前先以與腳本相同的 `pg_dump -Fc` 參數做臨時備份，在相同 PostgreSQL 映像、無具名 volume／無正式網路的臨時容器實際還原，核對教材、題組、作答與學習相關表筆數一致；只輸出筆數，完成即刪臨時容器與 dump。`ops/deploy` 仍照常另存部署備份並以 `pg_restore --list` 檢查，該備份保留到使用者驗收。套用後採前向修正；若還原部署前 DB，須先處理切換後新資料，不能直接啟動舊映像或丟棄新作答。

## 0015 目標日期與分天關卡

`0015_learning_goals.sql` 新增目標、排期／當日計量基準及版本更新提示的已讀資訊，對擁有者與教材有外鍵保護，不改寫既有題組、答案或完成規則。套用前備份產品 DB，並在隔離環境核對升級後原資料保留；同批備份只在新版部署驗證完成後取代前一份，不刪除其他既有備份。

前後端一起更新，才能顯示教材目標、首頁關卡與島嶼摘要。套用 0015 後，回復版本必須識別新 migration 與當日基準寫入規則；採相容修正版或前向修復，不直接重啟舊版，也不自動還原 DB 丟棄新的設定或作答。不需補跑模型或建立固定未來日程。

## 0017 文字筆記與個人註記

`0017_material_notes.sql` 新增筆記生成／快取及個人註記兩張表，不修改既有教材、題組或答案。套用前備份產品 DB；隔離環境由 0016 升級並逐表核對既有資料不變。新後端須與支援筆記分頁的前端一起部署。已套用後只能使用識別 0017 的相容版本或前向修復，不直接啟動舊 migration runner，也不還原 DB 丟棄新註記。

筆記使用目前正式Luna，生成契約為 `material-notes/v17`。新稿依原教材章節，模型核對一次後刪除不符內容，完整筆記每批對未涵蓋概念補寫並核對一次；複習速覽每章挑3～5條核心，缺漏概念明列。舊發布稿、Evidence與已保存lock/hash不改寫，契約變更重試只清除未發布暫存。runtime lock更新先建立同版provider bundle，再部署backend。

v16新增 `0032_note_source_sections.sql` 的 `material_notes.published_metadata`；同交易保存原章節與缺漏清單。需與理解 `sections`／`unorganized_concepts` 的前端一起部署。舊稿兩欄回空陣列；首次全空稿仍ready，有舊稿則以 NOTE_NO_SUPPORTED_CONTENT 保留舊稿。

公式匯出仍需 `backend/pyproject.toml`／`uv.lock` 鎖定的 `matplotlib==3.11.2` 與映像既有 `fonts-noto-cjk`；輕量更新以 `uv sync` 安裝依賴，首次排版會在 `$HOME/.config/matplotlib`（backend HOME為/tmp tmpfs）建立字型快取，部署後核對套件版本並匯出一份含公式的筆記。

部署後只對已授權帳號呼叫 `runtime.material_notes.generate(owner, material_id, revision, kind, expected_generation=...)` 重生指定稿，既有發布內容到新稿完成前保持可讀；`queue_existing` 只補本人缺少的複習速覽，不重生已存在工作。記錄真實provider呼叫數，不把worker嘗試數當模型次數。

## 0018 筆記生成診斷摘要碼

`0018_material_note_diagnostics.sql` 只在 `material_notes` 新增可為 NULL 的 `generation_diagnostics jsonb`，既有列保持 NULL，不改其他欄位或資料表；隔離環境由 0017 升級並逐表核對既有列不變。套用前備份產品 DB。只需更新後端（不需前端；可用輕量更新，不需完整重建 OCR 映像，也不改 runtime lock）；新後端讀寫此欄位，必須先套用 0018 才能啟動。已套用後回復版本須識別 0018（舊程式不讀此欄位，可相容），不還原 DB。欄位內容與日誌 `NOTE_GENERATION_ADJUSTED`／`NOTE_GENERATION_FAILED` 的 `diagnostics=` 相同，只含錯誤碼、事件、段落位置、重點編號、核對理由 SHA-256 前 12 碼與頁高比例；查詢時直接讀 DB，API 不回傳。

## 0019 成就紀錄（目標關卡與匯出）

`0019_badge_records.sql` 在 `learning_goals` 新增 `created_at`（既有列回填為目前的 `baseline_at` 後設為 NOT NULL，其他欄位不變），並新增 `goal_milestones`（分天關卡與準時達成目標，對教材 ON DELETE CASCADE）與 `learner_exports`（每種匯出第一次成功的時間，只記種類與時間）。套用前備份產品 DB；隔離環境由前一版升級並核對目標列除新欄位外不變。

新後端寫入新表與新欄位，必須先套用 0019 才能啟動，且前端要同時更新到接受 `xp-rules/v5`／`badge-rules/v8` 的版本。只改 Python 與前端，不需新增套件、不改 runtime lock，可用輕量後端更新（以 `docker inspect studydy-backend-1` 當下映像為基底）與前端輕量部署。已套用後回復版本須識別 0019，採前向修復，不還原 DB 丟棄新紀錄。部署後關卡、準時達成目標與匯出才開始累計，不補跑也不推測過去的紀錄。

## 0020 Podcast 影片選項、FLAC 音訊與帳號空間

`0020_podcast_audio_storage.sql`：`podcasts` 新增 `video_enabled`（既有列設為 true，維持原本自動生成影片；之後新建由程式明確寫入，沒有預設值）；`artifacts` 的 `podcast_audio` 允許 `audio/wav` 與 `audio/flac`；`podcast_videos` 新增 `removed` 狀態（刪除影片後保留 manifest、artifact 為 NULL）；新增空的 `storage_reservations`（帳號空間保留額度，見 [教材處理](materials.md#帳號空間)）。不搬資料；部署前已接受的工作沒有保留，照常完成。

- 只需 backend 輕量更新（映像沿用當下正式映像為底圖，FLAC 用既有 PyAV 編解碼，沒有新依賴或系統套件），再重建前端靜態檔；provider 不需更新或重啟（影片與對齊仍收到原 WAV）。backend 啟動時套用 migration，舊 backend 不認得 `removed` 與 `audio/flac`，因此不要回退到舊映像而保留新資料；回退前先確認沒有新建 FLAC 分集與已刪除的影片。
- 部署時進行中的分段上傳會因暫存檔改為帳號前綴而回報「上傳中斷」，重新上傳即可；舊暫存一天後由維護 worker 清除。
- 帳號空間上限 5 GiB 為程式常數 `runtime/storage_usage.py` 的 `ACCOUNT_STORAGE_LIMIT_BYTES`（每集預估的預設值也在同一處），調整後需重建 backend。部署後既有帳號若已超過上限，只會擋下新增，不刪除任何內容。

既有 WAV 轉 FLAC 使用維護腳本 `python -m runtime.podcast_audio_flac`，部署本身不會執行。建議在沒有 Podcast 生成、使用者較少的時段執行（替換瞬間正在讀同一檔的請求可能失敗一次，重試即可）：

~~~bash
docker compose exec backend /app/backend/.venv/bin/python -m runtime.podcast_audio_flac --dry-run
docker compose exec backend /app/backend/.venv/bin/python -m runtime.podcast_audio_flac --backup-dir /data/artifacts/.podcast-wav-backup --limit 1
docker compose exec backend /app/backend/.venv/bin/python -m runtime.podcast_audio_flac --backup-dir /data/artifacts/.podcast-wav-backup
~~~

dry-run 只讀取並在記憶體試轉，逐檔輸出 JSON 與合計（`wav_bytes`、`flac_bytes`、`saved_bytes`），不寫檔也不改 DB。正式執行全程持有一個執行層級的 advisory lock：另一個轉檔程序正在跑時直接以 `ANOTHER_CONVERSION_RUNNING` 結束；上次中斷的收尾（依 DB 狀態還原或刪除 `.staging` 中的舊 WAV，清掉 `*.flac-new`、`*.flac-new.*.tmp` 與備份目錄的 `*.wav.*.tmp`）、暫存寫入、提交與提交後刪除 WAV 都在鎖內，暫存檔名含每次執行的 ID，寫入或 fsync 失敗會刪除該次暫存。開始前核對轉換峰值空間：備份位置要放下全部待轉 WAV，artifact 磁碟要放下最大一檔的 FLAC 暫存（提交前舊 WAV 仍在）；兩者同一個檔案系統時合併計算。無損 FLAC 不保證比 WAV 小，因此每檔寫入備份與 FLAC 暫存前，再依實際大小核對當下剩餘空間；不足時該檔保留原狀、輸出 `stopped`，停止處理後續檔案，已完成的轉換不受影響。每檔先整檔核對 DB 雜湊並備份、讀回核對，再轉 FLAC，以獨立解碼比對 PCM 與還原後的整份 WAV，皆一致才在同一 artifact ID 換成 FLAC 並更新 `media_type／sha256／size_bytes`，最後刪除 WAV。任何一檔失敗都保留原狀並在輸出標 `failed`（結束碼 1），其他檔照常處理。分集 JSON（artifact ID 與原 WAV 的 `audio.sha256`）不變，所以既有影片、對齊、時間軸與收聽進度維持有效。備份位於主機 `data/artifacts/.podcast-wav-backup/<artifact>.wav`（容器身分 20001 可寫），抽查播放與影片無誤後由管理者刪除；它不計入帳號空間。

## 後端身分與既有資料

`STUDYDY_BACKEND_UID` 預設20001，GID沿用1000；它只控制 backend（含OCR）及其 runtime tmpfs，image 的 backend／OCR／test 預設身分也一致。完整建置時 Compose 以同名建置參數在 backend 階段建立 `studydy-backend` passwd 項目（HOME=/tmp tmpfs）；torch／transformers 以 `getpass`、`~` 決定身分與 cache，缺少此項目時 OCR 子程序會 CHILD_EXITED。改 UID 須完整重建 backend 映像，輕量更新沿用 runtime 映像內的項目。init 仍由0執行，bridge、cloudflared、download-ocr與主機Podcast維持原1000管理身分。SSH key與tunnel token不隨後端UID搬移或放寬權限。

既有artifacts需由核准部署程序先遷移owner，保留0700目錄及原0600／0400檔案契約。不得遞迴chown整個data；data根、data/podcast、PostgreSQL、模型內容及jerry分析資料保持原狀。模型僅授新後端UID只讀ACL。部署／回滾前應在停止該版backend寫入後重新盤點範圍，具體路徑、數量、操作順序與回滾命令由本次部署狀態檔提供，不在產品初始化時自動遷移。



## 學習資料匯出

exports 不需 migration。後端新增 `genanki`、`python-docx` 與 `fonttools`，依 `uv.lock` 安裝；沿用現有 PyMuPDF、PyAV 與映像內 `fonts-noto-cjk`。PDF 從 Noto 字型集合選取繁體中文標準／粗體並嵌入，不依賴瀏覽器列印；MP3 使用 PyAV 的 libmp3lame，不另起模型或轉檔服務。

輕量後端更新須以 `docker inspect studydy-backend-1` 當下映像為基底，保留 UID 20001 的 passwd 項目及原 OCR 環境。部署前核對中文字型標準／粗體、libmp3lame 與新增依賴可用。前端增加 `html-to-image`，完整渲染既有島嶼元件後產生 PNG；後端核對 owner、版本與完整概念集合後打包；匯出內容不寫入一般 log。

匯出以非同步 semaphore 同時最多處理兩份產檔；名額在 endpoint 函式結束時釋放，不延長到回應串流。既有 MP4 直接串流，不取得產檔名額。等待名額不佔用工作執行緒。卡組透過建立暫存、每批至多兩張 PNG 的 PUT、最後下載三個步驟傳輸，不把整組 base64 放進同一個請求。卡面、Anki SQLite 與輸出檔放在 artifact root 的私有 `.export-work/`，不使用只有 1 GiB 的 `/tmp`，不新增 DB migration。完成／取消／組檔失敗會清除該工作；維護 worker 清理超過 30 分鐘未更新且未鎖定的暫存。匯出與清理共用檔案鎖，下載尚未結束時不會被刪除。


卡組每份接受的 PNG 合計上限 512 MiB（按實際暫存 PNG 大小計算），每個概念最多 16 張、整份最多 2000 張；每個帳號最多同時保留兩份卡組匯出，包含正在下載、尚未清理的工作。建立工作以檔案鎖原子檢查帳號名額，暫存目錄名稱綁定 owner；超限分別回 `EXPORT_SIZE_LIMIT`（413）、`EXPORT_PAGE_LIMIT`（413）與 `EXPORT_ACTIVE_LIMIT`（409），前端顯示對應限制。打包成品另占磁碟空間，與 PNG／Anki SQLite 在下載結束後一起清理；卡組檔案 lease 保持到串流結束，不隨產檔名額提前釋放。同一工作正在上傳或下載時，重複操作回 `EXPORT_IN_PROGRESS`（409），不占用產檔名額等待檔案鎖。

口語分析的 `material-block-request/v2`／`material-block-response/v2` 與 `source-excerpts/v1` 必須與支援 `spoken_excerpt_meaning/v1`／`spoken_excerpt_literal_repair/v1` 的後端一同部署。只套用新分析；舊來源、已發布 Claim／Revision、runtime lock hash 不改寫。新 run 略過不相容的舊工作 checkpoint 並從頭分析，保留舊封存，不因新提示直接重用；回復版本須能讀取新投影，不能以改寫舊 hash 偽裝相容。

部署前要先清空舊 lock 的教材分析佇列，再切換後端與新 lock。

GEN-01 的引用補正新增逐項拒收診斷，並更新 material_semantics 提示；需一起更新後端與 runtime lock，不需 migration 或改動 provider。即使 schema 版本未變，提示變更仍會改變 lock hash，不沿用不相容的舊分析 checkpoint，新 run 會略過並重新分析，舊封存保留；依上述規則先清空舊 lock 的分析佇列，不改寫已封存 hash。

## 教材 worker 與自動重試

Migration 0022 新增 material_processing_runs.auto_retry_count／auto_retry_reason，不改舊 checksum 或來源 hash。先套用 migration 再啟動新後端；回復舊映像時保留新增欄位與已用重試次數，不刪資料。隔離測試驗證 SQL 套用及交易回滾。

STUDYDY_MATERIAL_WORKERS 預設 1，需多教材時由管理者在本機 .env 設定正整數後更新後端。provider 的教材分析／審查依 call-tags feature 分流，章節和其他文字功能仍用單一文字槽；STUDYDY_LUNA_PARALLEL_TEST=1 時教材分析與審查共用兩槽，增加 worker 不增加 provider 槽數。指定維護 run 不使用一般分析的自動重試。

## 學習紀錄與永久徽章

新增 learning_retained_days、learning_awards、learning_celebrations，原教材／作答／標記的資料格式不變。刪除先在同一交易保留每日統計與去重身分，再依原流程清內容；部署前已刪除而沒有紀錄的活動不能回補。既有帳號首次計算時保存目前已得徽章並將等級／徽章標記已看過，新帳號按原子領取端點取得新事件。

上線後若已有內容刪除，不可回退到不讀保留紀錄的舊後端，否則 XP 會暫時少算；需前向修復或相容映像。隔離 DB 驗證 migration 套用及交易回滾，不在產品資料庫刪除保留表回復。前端 G5 接線由前端批次交付。
## 隔夜教材與學習 API

本批新增 material_processing_runs.display_summary、materials.pending_goal、topic_scopes.learning_goal、material_notes.started_at；新migration隨分支整合依序編號，不改已套用SQL。隔離驗證套用／交易回滾，正式回復保留新增欄位，不刪使用者新保存的目標。

learning-goal/v2、learning-diagnosis/v2、material-notes/v3 與新回答 material-answer/v3 必須配合前端解析及接線部署；不能單獨上線造成舊前端拒收。原回答v2仍讀取，原來源、結構及runtime lock不改寫。

所有後端匯出的ZIP時間、PDF日期與Word標準日期欄位採Asia/Taipei（Word以+08:00標明），圖片ZIP依概念標題命名並去除非法字元、同名編號。地圖Markdown含完整重點、頁碼及每章Mermaid，需以實際Mermaid解析／渲染驗證特殊字元。

## 帳號空間細項

GET /v1/account/storage 升為 account-storage/v2，需與前端對應schema及G4接線配合部署，不提供舊schema相容回應。無migration或provider變更；總量與細項來自同一SQL與目錄掃描，並非DB／磁碟原子快照。

空間面板僅接受 `account-storage/v2`；須與 B3.4 的細項 API 同批部署。五類 breakdown 合計須等於 used_bytes，reserved_bytes 已包含其中；前 5 大教材使用 API 提供的順序與用量，不由前端重新掃描或估算。

## 作答狀態與問答限制

Q3 新增 assessment_presence migration，部署前需與前端 UUID browser header／作答心跳同批接線；舊文字、錄音、retry 呼叫若缺 header 會被拒絕。migration 只新增短期 presence 表，可在隔離 DB 以 transaction rollback 驗證；正式已套用檔不得改號或修改 checksum。

Q3 前端須與 B3.5 同批部署（feat/overnight-qa-guard，87e18dd01384ad31d97f1f626b66155561ac772f）；前端同步提供持久 ID、20 秒作答回報、交卷解除與 409 提示。取消及讀取既有內容不受新 header 限制。


PC1／PC2 使用 0026_podcast_step_times（在教材投影0024、作答presence0025之後）。須先更新 provider 的 /jobs/progress 與略過邏輯，再部署後端；未清空 Luna 工作不得重啟 provider。新增時間欄位可保留回復舊映像；不改既有講稿／音訊或核對封存。前端依已審 API 契約顯示步驟及略過提示。

### Podcast 步驟與略過提示前端

PC1／PC2 前端與 B3.6 `feat/overnight-podcast-progress`（94f342703d2656ff48ce7db85487a5655383130c）同批部署，依後端規定先更新 provider。部署順序為 B3.5＋Q3 → B3.6＋PC1／PC2 → B3.7＋V2。

V2使用0027_voice_sentences，順序在Q3的0025與PC1的0026之後。先部署provider音訊量測header，再部署後端與前端逐句播放接線；部署前沿既有規則清空工作，舊完成回答不建立假句序或補估時間。回復舊映像保留新增表／欄位，不刪已生成音訊。

### 逐句語音回答前端

V2 與 B3.7 `feat/overnight-voice-stream`（e8d191097285cf7f5f9bac921604433ca28c0134）同批部署；後端需先有 B3.6 並依其規則更新 provider。前端讀取公開 stage／audio_sentences，不以片段非空認定回答完成；未呼叫真模型量測延遲，不能把 mock 播放測試當作實際效能結果。

S2教材卡片合計新增material-library-item/v1.source_totals，與前端S2卡片同批部署；沒有migration或provider變更。未知影音合計為null、無影音為0。

L1確認設定與草稿目標DELETE需與前端L1同批部署，使用既有topic learning_goal及material pending_goal，無migration／provider變更。approve未傳保留、null清除；確認改設定後舊建立請求可回既有IDEMPOTENCY_CONFLICT，此限制已核准。

Q4正確選項投影與前端Q4同批部署：answer-feedback/v1新增可選correct_option_id，僅已交卷題組出現，舊已交卷資料直接投影保存的私密答案。無migration或provider變更，不回寫題目／作答紀錄。

S5移到新版與前端F6同批部署，套用migration0028_version_entry_state。前端F6須接受version-move-preview/v2、previous.move_id與return expected_move_id；不能單獨上線。既有材料初始化提醒狀態，不追溯自動移動；新發布才排入安全判定。

新題組採 `source-span-single-choice/v3`：模型輸出繁中答案與逐項解釋四選項的解析、原文 `source_quote`；來源子字串檢查改查引句。進行中的 v1／v2 題組仍以封存 lock 生成及驗證，已存 v1／v2 題目與原 hash 維持可讀；無資料回填或 migration。

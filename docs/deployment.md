# studydy.net 的 canonical 部署流程

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

## 前後端、provider 與 migration 的配套

- 後端回應的 schema 版本升級時（例如 `learning-activity/v4`＋`xp-rules/v5`＋`badge-rules/v8`、`account-sessions/v2`、`account-storage/v2`、`learning-goal/v2`、`learning-diagnosis/v2`、`material-notes/v3`、`material-answer/v3`、`version-move-preview/v2`），前端只接受對應版本，前後端必須同批上線、退回時也一起退回；只更新一邊會讓頁首等級、成就、個人檔案、帳號空間或筆記等畫面讀不到資料。舊資料（例如 v2 回答）仍依原格式讀取。
- runtime lock 有變更時，先建立同一版本的 provider bundle 並切換 provider，再部署 backend；部署前先清空舊 lock 的教材分析佇列。提示變更即使 schema 版本不變也會改變 lock hash，新 run 不沿用不相容的舊 checkpoint，已封存的 hash 不改寫。
- Podcast 講稿、核對與分鏡由 backend 呼叫文字模型，影片由 provider 分 prepare／render 兩段；backend 與 provider 介面改變時須在同一停機時段一起更新。重啟 provider 前等正在執行的 Podcast／影片／問答工作完成，不以取消使用者工作當作例行清理。
- 筆記公式匯出需要 `backend/pyproject.toml`／`uv.lock` 鎖定的 `matplotlib` 與映像內的 `fonts-noto-cjk`；首次排版會在 `$HOME/.config/matplotlib`（backend HOME 為 /tmp tmpfs）建立字型快取。部署後核對套件版本，並匯出一份含公式的筆記。學習資料匯出另依賴 `genanki`、`python-docx`、`fonttools` 與 PyAV 的 libmp3lame。
- 套用新 migration 後，回復用的後端映像必須能識別該 migration；採相容映像或前向修復，不直接啟動舊映像，也不為回退而還原 DB 丟棄新資料。
- 已有學習內容被刪除後，不可回退到不讀刪除前保留紀錄（`learning_retained_days` 等）的舊後端，否則 XP 會少算；需前向修復或相容映像。
- 依賴版本以 lock 為準：Pillow 在後端、影片渲染與 OCR 環境為 13.0.0，CosyVoice 環境維持 12.3.0；PyMuPDF 1.28.0。正式 Podcast 環境的套件更新須建立新環境並經驗收，不直接修改正在服務的 venv。

## 後端身分

`STUDYDY_BACKEND_UID` 預設20001，GID沿用1000；它只控制 backend（含OCR）及其 runtime tmpfs，image 的 backend／OCR／test 預設身分也一致。完整建置時 Compose 以同名建置參數在 backend 階段建立 `studydy-backend` passwd 項目（HOME=/tmp tmpfs）；torch／transformers 以 `getpass`、`~` 決定身分與 cache，缺少此項目時 OCR 子程序會 CHILD_EXITED。改 UID 須完整重建 backend 映像，輕量更新沿用 runtime 映像內的項目。init 仍由0執行，bridge、cloudflared、download-ocr與主機Podcast維持原1000管理身分。SSH key與tunnel token不隨後端UID搬移或放寬權限。

artifacts 維持0700目錄及0600／0400檔案契約。不得遞迴chown整個data；data根、data/podcast、PostgreSQL、模型內容及jerry分析資料保持原狀。模型僅授後端UID只讀ACL。

## Podcast 與 Studydy AI BOT 的語音流程

正式 Podcast 使用原參考聲線、V／W 台灣華語指令、整句中英混讀及通用接點／停頓／語速規則。處理直接作用於模型片段及 speaker 邊界，不使用人工指定秒數或 WAV 句界推測；既有音訊保留。

Studydy AI BOT 共用 Podcast 的整句混讀、台灣口音指令、原 host 聲線、停頓修正與音量處理。provider 每次音訊請求啟動同一個已部署 runtime bundle 的 `ops/podcast/synthesize.py`；腳本更新需建立新 bundle 並切換 provider，不能改動正在使用的版本。

新語音使用 `dynamic-pauses/v2`（規則見 `ops/podcast/dialogue_audio_repair.py` 的 `pause_seconds`）：換段 1100ms；同一人以逗號續講 220ms、分號 350ms；換人前的簡短接話 350ms；6 字以內短句續講 250ms；問句與一般換人 650ms；20 字以上長句 850ms，其餘句尾 550ms。字數依原講稿的文字與數字計算，縮寫與數值的朗讀展開不改變分類；符合一個條件就選定停頓，不疊加，現有片段兩端靜音計入目標。既有影音不改時間軸，新建節目以新音訊重新對齊字幕及影片。

講稿逐段核對（`source-script/v17`）的提示存於 runtime lock 的 `podcast_script_review`（`podcast-script-review/v2`）；backend 的 `podcast_script_review` 日誌記實際呼叫次數。程式與 lock 不一致時記 `PODCAST_REVIEW_CONTRACT_INVALID`，節目顯示為 `PODCAST_PROVIDER_FAILED`。

## 媒體來源（圖片、音訊、影片、YouTube）

- 後端 Python 依賴 `av==19.0.1`（PyAV，wheel 內含 FFmpeg，其中包括 GPL 授權的 libx264／libx265 編碼器；本站以網站服務提供，不散布映像或執行檔，轉檔只用解碼與 AAC／Opus 編碼）與 `yt-dlp==2026.10.21`（Unlicense），記在 `backend/pyproject.toml`／`uv.lock`。沒有新系統套件，輕量更新的 `uv sync` 即會安裝，不需完整重建 OCR 映像；部署後在容器內核對 `import av, yt_dlp` 與版本。
- 主機 provider 提供 `/media-transcribe` 與 `ops/podcast/transcribe_media.py`（顯示卡辨識，沿用 STT 環境與 CUDA `LD_LIBRARY_PATH`）。provider 介面變更時先更新並重啟 provider，再更新 backend。
- 分段上傳與轉換暫存在 `data/artifacts/.media-work/`；單支影片暫存最多 2 GiB，部署主機需保留對應空間。中斷超過一天的分段上傳與轉換暫存（含程序被終止時留下的原檔副本與 YouTube 音軌）由維護 worker 清除。
- YouTube 改版造成讀取失敗時，更新 `yt-dlp` 版本並重建後端；失敗的來源可重試。

## 免費通用搜尋

`research-search` 使用鎖定 digest 的 SearXNG 映像與 `ops/search/settings.yml`，不發布主機 port，也不需要付費 API key。初次接入先以原本的 HTTPS Compose 組合執行 `up -d --no-deps --wait research-search`，再更新 backend／frontend。搜尋用的內部簽署密鑰在啟動時產生，不寫入版本庫。

搜尋關鍵詞提示位於主機的 `ops/podcast/provider.py`；更新時要保留已執行中的 Podcast／影片／問答工作，確認工作完成的安全空檔後才重啟 provider 與 backend。不能把取消整份 Podcast 當作部署前的例行清理。搜尋服務失敗會保留其他搜尋結果及部分失敗狀態。

## 維護指令

### 補建讀取索引

保存必要備份與實際映像身分後，使用新建的 backend image 執行：

```bash
docker compose --env-file .env -p studydy -f compose.yaml -f compose.tunnel.yaml \
  -f compose.app-update.yaml run --rm --no-deps backend prepare-read-indexes
```

此命令先套用 migration，再逐版本驗證並補建缺少的導覽、學習與概念讀取索引，同時從 canonical Evidence 補回關係缺少的原文、頁碼與來源位置；可重跑、不呼叫模型，完成後重跑回報 0。教材、revision、原檔及學習紀錄不重寫。不要在一般 GET 交易內補建大型索引；缺少索引的正常讀取回報 `KNOWLEDGE_STRUCTURE_UNAVAILABLE`，不隱式寫入。

### Podcast 音訊 WAV 轉 FLAC

既有 WAV 轉 FLAC 使用維護腳本 `python -m runtime.podcast_audio_flac`，部署本身不會執行。建議在沒有 Podcast 生成、使用者較少的時段執行（替換瞬間正在讀同一檔的請求可能失敗一次，重試即可）：

~~~bash
docker compose exec backend /app/backend/.venv/bin/python -m runtime.podcast_audio_flac --dry-run
docker compose exec backend /app/backend/.venv/bin/python -m runtime.podcast_audio_flac --backup-dir /data/artifacts/.podcast-wav-backup --limit 1
docker compose exec backend /app/backend/.venv/bin/python -m runtime.podcast_audio_flac --backup-dir /data/artifacts/.podcast-wav-backup
~~~

dry-run 只讀取並在記憶體試轉，逐檔輸出 JSON 與合計（`wav_bytes`、`flac_bytes`、`saved_bytes`），不寫檔也不改 DB。正式執行全程持有一個執行層級的 advisory lock：另一個轉檔程序正在跑時直接以 `ANOTHER_CONVERSION_RUNNING` 結束；上次中斷的收尾（依 DB 狀態還原或刪除 `.staging` 中的舊 WAV，清掉 `*.flac-new`、`*.flac-new.*.tmp` 與備份目錄的 `*.wav.*.tmp`）、暫存寫入、提交與提交後刪除 WAV 都在鎖內，暫存檔名含每次執行的 ID，寫入或 fsync 失敗會刪除該次暫存。開始前核對轉換峰值空間：備份位置要放下全部待轉 WAV，artifact 磁碟要放下最大一檔的 FLAC 暫存（提交前舊 WAV 仍在）；兩者同一個檔案系統時合併計算。無損 FLAC 不保證比 WAV 小，因此每檔寫入備份與 FLAC 暫存前，再依實際大小核對當下剩餘空間；不足時該檔保留原狀、輸出 `stopped`，停止處理後續檔案，已完成的轉換不受影響。每檔先整檔核對 DB 雜湊並備份、讀回核對，再轉 FLAC，以獨立解碼比對 PCM 與還原後的整份 WAV，皆一致才在同一 artifact ID 換成 FLAC 並更新 `media_type／sha256／size_bytes`，最後刪除 WAV。任何一檔失敗都保留原狀並在輸出標 `failed`（結束碼 1），其他檔照常處理。分集 JSON（artifact ID 與原 WAV 的 `audio.sha256`）不變，所以既有影片、對齊、時間軸與收聽進度維持有效。備份位於主機 `data/artifacts/.podcast-wav-backup/<artifact>.wav`（容器身分 20001 可寫），抽查播放與影片無誤後由管理者刪除；它不計入帳號空間。

## 必要驗證工具

`ops/tests/compose_boundary.py` 使用明確的 `STUDYDY_TEST_ENV_FILE`，只列安全摘要。
`ops/tests/nginx_boundary.py` 使用指定測試 images、隔離 echo upstream 與 loopback 4183。
Backend runtime tests 使用 disposable DB；不以產品資料跑測試，不因部署檢查呼叫 Pod／Gemma。
純文件整理只檢查命令／路徑與 Compose metadata，不需重建線上 images。

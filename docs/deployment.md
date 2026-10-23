# studydy.net 的 canonical 部署流程

唯一 deployment working directory 是 `/home/jerry/project/Studydy/main`。
日常 rebuild、Compose 管理、設定與 source-of-truth 都在這個 checkout；build context
是本目錄 `.`，不需要先複製 source 到其他目錄。

## 032 終態接軌至 7 份領域 baseline

新後端不自動改寫舊 migration 帳本。既有站點切換前，先在獨立的 DB／Artifact 副本完成
`runtime.storage.baseline check`、`apply` 與讀取驗證，再於正式站停止寫入、保存配對備份及舊映像後，
以新映像執行同一個維護入口。入口使用 `STUDYDY_DATABASE_DSN`（或既有 `POSTGRES_*` 設定）與
`STUDYDY_ARTIFACT_ROOT`；不必載入 API 或啟動 worker。

`python -m runtime.storage.baseline apply` 會在交易內重新鎖表，核對舊 32 份 checksum、實體 schema、
全部業務資料、內容身分與檔案 hash。只有完整相符才保存舊帳本收據並接軌；任何驗證失敗都回滾。
核對成功後才啟動新後端，持久資料掛載仍使用正式站的 `main/data/`，不得改接驗證副本。

若尚未增加後續 migration，可在停寫後由新映像執行
`python -m runtime.storage.baseline restore-ledger`，重新驗證並只回復舊帳本，再切回原後端映像。
這不還原業務資料或覆蓋部署後的新內容；未知 schema、被修改的 checksum 或後續 DDL 都會拒絕。
詳細操作與副本驗證見 [安裝文件](getting-started.md)。

從核准的獨立分支部署時，Compose 管理目錄與資料仍固定在 `main`，映像身分記錄實際分支 SHA。
主機 Podcast provider 與每次請求啟動的子程序必須使用同一個已驗證 SHA 的唯讀 runtime bundle，
保存於 `main/data/deployments/<release>/provider-source`，不要直接載入仍在修改的分支 checkout。
bundle 只包含需要的 `backend/src`、`ops` 與 `local_ai/runtime-lock.json`，沿用原 Python／模型環境、
瀏覽器依賴與 `STUDYDY_VIDEO_WORK_DIR`；以 `STUDYDY_PODCAST_JOB_LOG` 明確指向原有工作日誌。
切換前確認沒有進行中的工作，再更新 service 的 `WorkingDirectory`／`ExecStart` 並重啟。
同時記錄 bundle SHA、檔案 hash 與實際啟動路徑，避免容器與主機 provider 運行不同版本。

首次部署曾以 committed SHA 的 `git archive` 匯出到 `/tmp/studydy-main-release-*/source`
做建置與驗證。這些是一次性歷史證據，不是目前 production working directory、資料來源，
也不是下次 rebuild 的輸入。容器內 `/tmp` 的 Nginx 設定與 cache 是 tmpfs，不是另一份 source。

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
- 私有 `.env` 留在本 checkout；token 檔留在 repo 外
  `/home/jerry/.config/studydy/cloudflared-main-token`，只由 cloudflared 唯讀掛載。
  不讀出、複製或雜湊 token；只檢查 metadata，不把值放進 environment、image 或紀錄。
- HTTPS origin 是 `https://studydy.net`，Secure cookie=true。維護入口不放寬 exact Origin。
- 已確認 Cloudflare zone 上限為 100 MB；有效新上傳上限為 `94371840` bytes（90 MiB），
  Nginx／API 共用設定，UI 讀取 capabilities。既有 artifact 讀取權限不變。
- 已核准公開 Internet 部署；Access 為 optional。沿用目前 Cloudflare 設定，不需為 rebuild
  重新建立 Tunnel、DNS 或憑證。私有 API 保留 `private, no-store`，不強制公共快取。

## Podcast 試聽結束後的正式套用

已移除公開比較頁與試聽 WAV、Compose 靜態掛載。`/podcast-voice-comparison` 及其子路徑回 404，避免舊網址回到 SPA。正式 provider 已改採 V／W 指令與 v13 整句混讀；舊 Podcast 音訊保留，新建內容套用新版。回復資訊保存在 `data/deployments/podcast-b-v11-*/`，私有 provider 設定備份不輸出或提交。

教材問答也共用 Podcast 的整句混讀、台灣口音指令、原 host 聲線、停頓修正與音量處理。舊的中英文分段合成分支已刪除，沒有 legacy 開關；既有音訊保留，新合成與重試使用共用流程。provider 每次音訊請求啟動同一個已部署 runtime bundle 的 `ops/podcast/synthesize.py`；腳本更新需建立新 bundle 並切換 provider，不能改動正在使用的版本。

新語音統一使用 `dynamic-pauses/v1`：短句續講 250ms、一般句尾 550ms、20 字以上長句 850ms、問句與一般換人 650ms、簡短接話前 350ms、換主題 1100ms。依原講稿的文字與數字計數，短句為 6 字以內；縮寫與數值的朗讀展開不改變分類。換段優先，符合一個條件就選定停頓，不疊加；現有片段兩端靜音計入目標。固定停頓機制與臨時試聽設定已移除，`/pause-preview` 及其資產回 404。既有影音不改時間軸，新建節目以新音訊重新對齊字幕及影片。

跨語言教材更新需一起部署後端與 runtime lock。新分析寫入 `localized_meaning_v2`，以完整數量 token 保護短數值；既有 `localized_meaning`／`semantic_meaning` 仍依各自已保存的規則讀取。寫入 v2 後，回復版本必須能讀取 v2；第一輪 `816150aa` 映像單獨使用並不相容，需經驗證的相容映像或前向修復。原有知識結構與來源不搬移、不改 hash；舊分析提示的 checkpoint 不與新的翻譯政策混用。

## 核准 source 與直接建置

一般 Git 整合仍為 `feature/* → dev → main`。學生專題需要外網、手機或語音實測時，
可從使用者當次核准的 branch／完整 SHA 直接部署到同一個 studydy.net；不另建 dev domain。
部署 SHA 以本次實際建置的 commit 為準，不假設一定等於最新 main，也不以 branch 名取代 SHA。

以下命令在未來獲准 rebuild 時執行，本次文件整理不執行 build／up：

```bash
cd /home/jerry/project/Studydy/main
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

Compose build 本身不自動加入 Git revision label。若要像首次部署一樣在 image 中保留
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

## Podcast migration 0008 的回復界線

本次正式切換已保存資料庫 dump、artifact 備份與原映像身分；紀錄位於 `data/deployments/podcast-*/source-manifest.json`，皆留本機私人目錄。來源為已批准的未提交工作目錄，映像以 `working-tree:<snapshot hash>` 明示，不冒充乾淨 Git SHA。

0008 已套用後，只有 0001–0007 的舊 backend 會拒絕 migration 帳本，因此不能只換回舊 backend tag。需保持可識別 0008 的後端並作前向修復，或準備經隔離驗證的相容回復版本。還原到部署前 DB 會失去部署後新增資料，不能自動覆蓋現有 DB；必須先另行核定資料保留與切換方案。舊 frontend 可與目前後端的既有 API 配合回復介面。

0009 將已保存的 Podcast 講稿統一為發言輪次，不保留舊格式相容路徑。套用後必須使用識別 0009 的後端；不能直接退回只包含 0008 的映像。升級前備份，升級後核對原文字／來源與音訊 hash，失敗時保留匹配 schema 的映像作前向修復，不自動覆蓋產品 DB。

0017 新增補充查詢的 nullable `deleted_at`。刪除查詢會隱藏歷史紀錄並撤銷尚未送出工作的 lease，保留候選中的來源關係、教材、原始檔案及已送出的分析。升級前保存本機 DB dump；沒有原檔搬移或刪除。套用後保留識別 0017 的後端作前向修復，不直接退回舊後端或覆蓋產品 DB；前端可單獨回復先前映像。

## V／W 正式整合與試聽清理（2026-10-08）

使用者選定 VW-2 並授權正式套用。Podcast 使用原參考錄音、V／W 台灣華語指令、整句中英混讀，以及通用接點／停頓／語速規則。直接在模型片段邊界處理，不將人工指定秒數的診斷腳本或 WAV 句界推測接入產品。經驗與保護條件見 [測試文件](testing.md)。

臨時 `/voice-compare-*` 頁面及 WAV、Compose 掛載、BreezyVoice PoC source／模型／快取、本輪舊試聽產物和專用產生工具已移除；舊網址與其資產回 404，不回 SPA。只保留小型聽評摘要、正式程式及部署回復資料，沒有把整包實驗環境搬進 archive。

部署資料位於 `data/deployments/podcast-vw-20261008/`：包含前後 source hash、實際映像身分、必要 source 回復快照、資格測試摘要及清理清單。資格測試的新正式合成路徑與已接受的 VW-2 WAV 逐位元一致。工作樹仍有先前已授權變更，使用 source snapshot 身分，不冒充乾淨 HEAD；本次沒有 commit／push 或 DB migration。

回復服務時保留試聽入口的 404 與已移除的掛載，不恢復已刪除測試音訊。分集器與 provider 的容量必須同步；已建立的較長分集不能回到只接受六個 claim 的寫稿入口。產品 DB、教材原檔、既有 Podcast、正式參考音及共用環境均保留。

本次指定教材的新作品 ID 保存於本機部署紀錄，兩集音訊約 1:47／2:25，皆由新正式 provider 產生。腳本 v9 增加有界的引用契約修稿與語意強度提示；審查仍 blocking。未生成成功的候選不發布，未將診斷稿直接寫入產品。

本次兩集音訊與教學影片最終均已完成；第二集影片首次來源／教學審查未過，沿原標準重試後通過，未改寫音訊。正式播放器以 1× 實播兩集、第二集音訊與影片跳轉至 120 秒均正常；原教材的需複核標記保留。

## 動畫實驗撤回（2026-10-09）

已撤回 Motion Canvas／通用動畫 PoC 與本輪 interaction 擴充，移除 animation-review 服務及臨時 Compose 掛載。`/animation-poc-review` 與子路徑回 404；部署回到 compose.yaml＋compose.tunnel.yaml。既有講稿、TTS、字幕播放、歷史 MP4 與產品資料保留。撤回的 source 差異、映像身分與本機 DB 備份位於 `data/deployments/podcast-animation-withdrawal-20261009/`；不重寫 Git 歷史或產品資料。

## 開源視覺生成實驗已撤回（2026-10-09）

依使用者要求刪除本輪視覺 PoC：移除 `podcast-visual-test` 的臨時 Compose 掛載、本機／E 槽成果、專用執行環境與 Z-Image／Wan／VACE 測試權重。部署恢復 `compose.yaml`＋`compose.tunnel.yaml`；只更新 Nginx 撤站路由，前端產品 bundle 沿用原部署，後端、DB、TTS、既有 Podcast 與教材未回退或刪除。

`/podcast-visual-test` 及其子路徑在 origin 回 404。撤回與實際新建 Podcast 回測的必要紀錄位於 `data/deployments/podcast-visual-removal-*/`。Cloudflare 的舊快取及先前誤建的 ChatGPT Sites 歷史副本，需依該紀錄的清理狀態判斷，不因 origin 檔案刪除就宣稱外部副本已清空。

## 免費通用搜尋

`research-search` 使用鎖定 digest 的 SearXNG 映像與 `ops/search/settings.yml`，不發布主機 port，也不需要付費 API key。初次接入先以原本的 HTTPS Compose 組合執行 `up -d --no-deps --wait research-search`，再更新 backend／frontend。搜尋用的內部簽署密鑰在啟動時產生，不寫入版本庫。

搜尋關鍵詞提示位於主機的 `ops/podcast/provider.py`；更新時要保留已執行中的 Podcast／影片／問答工作，確認工作完成的安全空檔後才重啟 provider 與 backend。不能把取消整份 Podcast 當作部署前的例行清理。搜尋服務失敗會保留其他搜尋結果及部分失敗狀態。

## 按需教材讀取索引（0027）

`0027_knowledge_read_indexes.sql` 新增導覽／學習規則及單筆 Evidence 定位投影，沿用 `card_source_concepts` 的完整概念內容。原始 PDF、canonical document、revision 與作答事件不變。新發布教材會在同一交易內建立索引；舊教材必須補建後才切換新版前端。

保存 DB dump、來源 diff／新增檔案及實際映像身分後，使用新建的 backend image 執行：

```bash
docker compose --env-file .env -p studydy -f compose.yaml -f compose.tunnel.yaml \
  -f compose.app-update.yaml run --rm --no-deps backend prepare-read-indexes
```

此命令先套用 migration，再逐版本驗證並補建缺少的索引；可重跑、不呼叫模型。確認沒有進行中的發布工作，更新 backend，再重跑補建以涵蓋切換期間完成的舊版工作，最後更新 frontend。不要在一般 GET 交易內補建大型索引。

此命令也會修復舊概念索引遺漏的關係專屬 Evidence：保留既有 Claim 節錄，只從 canonical Evidence 補回關係缺少的原文、頁碼與來源位置，原地更新衍生 payload／hash。教材、Revision、原檔及學習紀錄不重寫；完成後重跑回報 0。缺少概念或學習索引的正常讀取回報 `KNOWLEDGE_STRUCTURE_UNAVAILABLE`，不隱式寫入。

舊完整教材／resume API 暫時保留給部署前已開啟的頁面；新版使用 outline、concepts、map-progress 與 workspace 讀取。API 仍為 `private, no-store`，每次核對 owner、發布版本、runtime／來源綁定與投影雜湊。瀏覽器記憶體快取隨帳號 client 清除，不存入 localStorage。

0027 套用後不可直接重啟不認識該 migration 的舊 backend；採相容新 schema 的前向修正，不自動還原 DB。前端可單獨退回先前映像，其完整 API 仍可使用。

## 需求確認與研究原檔（0028／0029）

2026-10-16 於既有正式環境套用：0028 將補充規劃綁定教材與 revision；0029 增加本機研究 PDF 快照，保留第一次閱讀的原檔供後續選取。兩者不改寫既有教材、原檔或歷史研究。新版須同時更新前後端；舊直接建立研究入口已移除，新研究一律先確認範圍。

本次以線上映像為基底，只覆寫本任務的 14 個後端 source／migration 檔案及前端靜態 bundle，既有 Podcast renderer 與 Nginx 設定逐位元保留。部署身分是已授權工作樹快照，不冒充乾淨 Git commit。必要測試及模型驗收限制見 [測試文件](testing.md#需求確認與論文語意搜尋2026-10-16)。DB dump、逐檔 hash、實際映像與公開確認流程驗收保存在本機 `data/deployments/research-scope-20261016T140548Z/`。

升級後使用識別 0028／0029 的後端前向修復，不能直接啟動舊 migration 帳本或自動以部署前 DB 覆蓋新資料。快照清理只作用於研究準備表；已匯入教材的 Artifact 仍依既有生命週期管理。

## 論文取得與證據判讀（2026-10-17）

在既有正式環境更新五個研究 runtime 檔案：首次閱讀的公開替代版本、PDF 優先及 HTML 正文補救、文字快照，以及保留有限證據的閱讀／涵蓋規則。沿用 0029 的研究準備表，沒有新增 SQL migration；前端、Podcast renderer 與共用依賴沿用原部署。舊工作續接時重核不同政策的涵蓋快取，原檔與已讀引用保留。

本機 `data/deployments/research-quality-20261016T162315Z/` 保存部署前 DB dump、五個 source 的快照／hash、實際映像、必要模型與原文定位證據，以及公開入口驗收。升級後的文字快照需由支援該格式的後端讀取，故採前向修正，不直接切回只識別 PDF 快照的舊程式或覆蓋產品 DB。驗證結果與尚存缺口見 [測試文件](testing.md)。

## 0008 個人顯示名稱（2026-10-21）

0008 只在 `learners` 新增 nullable `display_name` 與 CHECK，不回填、不改寫既有資料；baseline 仍只鎖定 0001–0007，接軌後由啟動流程套用 0008。上線前在正式 DB dump 的一次性副本上執行，30 張業務表資料不變。套用後不能直接重啟只識別 0001–0007 的舊後端，採前向修正；前端可單獨退回。

## 0009 概念卡複習（2026-10-22）

0009 新增 `card_review_events`、`card_review_states` 兩張表，對卡組外鍵為 ON DELETE CASCADE，不修改既有資料。上線前在正式 DB dump 的一次性副本（等 PostgreSQL 初始化完成兩次啟動後才還原，並以 `--exit-on-error` 確認還原成功）套用，原 30 張業務表資料不變。套用後需使用識別 0009 的後端，採前向修正；前端可單獨退回。

## 0010 Podcast 收聽進度（2026-10-22）

0010 新增 `podcast_episode_progress`、`podcast_listening_days` 兩張表，對 Podcast 外鍵為 ON DELETE CASCADE，不修改既有資料。上線前須同 0009 先在正式 DB dump 的一次性副本套用，並核對原業務表資料不變。後端同時改為 `learning-activity/v2`，前端需一起更新（舊前端讀到 v2 時只會隱藏本週節奏）；套用後需使用識別 0010 的後端，採前向修正。

## 0011 教材新版承接（2026-10-22）

0011 新增 `material_version_moves`（學生確認「移到新版」的請求與重送結果）、`card_set_moves`（舊卡組→新版卡組及概念對應）、`study_session_moves`（舊版學習→新版學習及承接的已完成概念）三張表。舊卡組與學習需要能查詢的「已移轉」連結、合併時的卡片身分，以及整次確認的冪等紀錄，既有欄位沒有可用位置，因此追加 migration。對教材、卡組與學習的外鍵皆為 ON DELETE CASCADE（教材清除時一併刪除），不修改既有資料。上線前同 0009／0010 先在正式 DB dump 的一次性副本套用並核對原業務表資料不變；套用後需使用識別 0011 的後端，採前向修正。前端可單獨退回（舊前端不顯示「有新版本」，已承接的資料照常可讀）。

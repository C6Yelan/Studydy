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

Pillow 固定為 13.0.0，涵蓋後端 test extra、Podcast 影片與 CosyVoice requirements，以及 OCR 獨立環境。OCR 使用 Pillow 解析 PyMuPDF 重繪的 PNG；升級須核對相同合成 PDF 的文字與座標，影片另核對實際渲染及字幕排版。

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
- 已確認 Cloudflare zone 上限為 100 MB；有效新上傳上限為 `94371840` bytes（90 MiB），
  Nginx／API 共用設定，UI 讀取 capabilities。既有 artifact 讀取權限不變。
- 已核准公開 Internet 部署；Access 為 optional。沿用目前 Cloudflare 設定，不需為 rebuild
  重新建立 Tunnel、DNS 或憑證。私有 API 保留 `private, no-store`，不強制公共快取。

## Podcast 與教材問答的語音流程

正式 Podcast 使用原參考聲線、V／W 台灣華語指令、整句中英混讀及通用接點／停頓／語速規則。處理直接作用於模型片段及 speaker 邊界，不使用人工指定秒數或 WAV 句界推測；既有音訊保留。

教材問答也共用 Podcast 的整句混讀、台灣口音指令、原 host 聲線、停頓修正與音量處理。舊的中英文分段合成分支已刪除，沒有 legacy 開關；既有音訊保留，新合成與重試使用共用流程。provider 每次音訊請求啟動同一個已部署 runtime bundle 的 `ops/podcast/synthesize.py`；腳本更新需建立新 bundle 並切換 provider，不能改動正在使用的版本。

新語音統一使用 `dynamic-pauses/v1`：短句續講 250ms、一般句尾 550ms、20 字以上長句 850ms、問句與一般換人 650ms、簡短接話前 350ms、換主題 1100ms。依原講稿的文字與數字計數，短句為 6 字以內；縮寫與數值的朗讀展開不改變分類。換段優先，符合一個條件就選定停頓，不疊加；現有片段兩端靜音計入目標。固定停頓機制與臨時試聽設定已移除，`/pause-preview` 及其資產回 404。既有影音不改時間軸，新建節目以新音訊重新對齊字幕及影片。

跨語言教材更新需一起部署後端與 runtime lock。新分析寫入 `localized_meaning_v2`，以完整數量 token 保護短數值；既有 `localized_meaning`／`semantic_meaning` 仍依各自已保存的規則讀取。寫入 v2 後，回復版本必須能讀取 v2；需經驗證的相容映像或前向修復。原有知識結構與來源不搬移、不改 hash；舊分析提示的 checkpoint 不與新的翻譯政策混用。

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

## 後端身分與既有資料

`STUDYDY_BACKEND_UID` 預設20001，GID沿用1000；它只控制 backend（含OCR）及其 runtime tmpfs，image 的 backend／OCR／test 預設身分也一致。init 仍由0執行，bridge、cloudflared、download-ocr與主機Podcast維持原1000管理身分。SSH key與tunnel token不隨後端UID搬移或放寬權限。

既有artifacts需由核准部署程序先遷移owner，保留0700目錄及原0600／0400檔案契約。不得遞迴chown整個data；data根、data/podcast、PostgreSQL、模型內容及jerry分析資料保持原狀。模型僅授新後端UID只讀ACL。部署／回滾前應在停止該版backend寫入後重新盤點範圍，具體路徑、數量、操作順序與回滾命令由本次部署狀態檔提供，不在產品初始化時自動遷移。


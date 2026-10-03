# studydy.net 的 canonical 部署流程

唯一 deployment working directory 是 `/home/jerry/project/Studydy/main`。
日常 rebuild、Compose 管理、設定與 source-of-truth 都在這個 checkout；build context
是本目錄 `.`，不需要先複製 source 到其他目錄。

首次部署曾以 committed SHA 的 `git archive` 匯出到 `/tmp/studydy-main-release-*/source`
做建置與驗證。這些是一次性歷史證據，不是目前 production working directory、資料來源，
也不是下次 rebuild 的輸入。容器內 `/tmp` 的 Nginx 設定與 cache 是 tmpfs，不是另一份 source。

## 固定部署邊界

- Compose project：`studydy`；公開設定必須同時使用 `compose.yaml`＋`compose.tunnel.yaml`。
  單用 base 檔的初次本機流程，不適用於目前公開站的日常 redeploy。
- cloudflared 僅 edge；frontend 為 edge＋application；backend／model bridge 的網路不變，
  database 保持 internal。公開 route 為 `studydy.net → http://frontend:8080`。
- 維護入口維持 `127.0.0.1:4173`，backend／DB／model／metrics 不發布 host ports。
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

已移除公開比較頁與試聽 WAV、Compose 靜態掛載。`/podcast-voice-comparison` 及其子路徑回 404，避免舊網址回到 SPA。正式 provider 採用 B 聲線及 v11 對談；舊 Podcast 音訊保留，新建內容套用新版。回復資訊保存在 `data/deployments/podcast-b-v11-*/`，私有 provider 設定備份不輸出或提交。

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

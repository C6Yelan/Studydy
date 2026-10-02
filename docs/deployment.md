# studydy.net 部署準備

Deployment preparation only; Cloudflare activation not performed.

部署採 WSL native Docker，沿用 frontend Nginx。`compose.yaml` 保留本機模式；加上
`compose.tunnel.yaml` 才選用 `https://studydy.net` 與 Secure cookie。

- cloudflared 僅 edge；frontend 為 edge＋application。
- backend 為 application＋database；model-bridge 僅 application；database 為 internal。
- 唯一 host publishing 是 `127.0.0.1:${STUDYDY_PORT:-4173}:8080`。
- candidate 使用獨立 project、disposable DB／artifact 與 `STUDYDY_PORT=4183`。
- 不為 localhost 開放第二個 Origin。正式 HTTPS 設定下，localhost 僅供靜態頁面與診斷。

## 設定契約

`STUDYDY_UPLOAD_MAX_BYTES` 同時傳入 backend 與 Nginx，capabilities 與選檔 UI 跟隨
有效上限；內部 artifact／conversion hard maximum 仍為 104857600 bytes。
公開 override 要求明確設定此值。94371840 bytes（90 MiB）只是測試候選：
**NEEDS CLOUDFLARE CHECK BEFORE D**。既有較大 artifact 仍可讀取。
capabilities 失敗時暫停選檔；413／429／入口拒絕只顯示固定訊息，不渲染任意 HTML。

Nginx 拒絕未知或缺少 Host，接受 studydy.net／127.0.0.1／localhost。
未使用的 forwarded、client-IP、Access 身分 headers 在 API proxy 被移除；Cookie、
Origin 與產品 headers 保留。API 不新增 real-IP 或 trust-all proxy 設定。

## Token file 與 D 的人工操作

cloudflared 固定 2026.9.3 及官方 digest；只用 `--token-file`。
`STUDYDY_TUNNEL_TOKEN_FILE` 是 **repo 外 Linux filesystem 的絕對路徑**，不是 token 值。
使用者在 D 私下建立受限目錄（0700）與檔案（0600），檔案 owner 須符合
`STUDYDY_UID`／`STUDYDY_GID`。Compose 不會自動改 bind file 的權限。
不將 token 放進 `.env`、shell command、image、log 或聊天。

使用者須在 D 前確認 zone upload limit、受邀 Access 或替代措施、最終 main SHA 與
維護時段，再建立 named Tunnel／token、唯一 Published application
`studydy.net → http://frontend:8080`、DNS、有效憑證、HTTP→HTTPS 與 `/v1/*` cache bypass。
不建立 wildcard／SSH／TCP route。Access 不作 Studydy learner identity。

本輪不執行正式啟動命令。未來獲准後才使用 `compose.yaml`＋`compose.tunnel.yaml`
及 `tunnel` profile；模型仍按既有 ssh profile 管理。Token mount 驗證與 Cloudflare
readiness 是不同 gate；無 token 時不宣稱 connector Healthy。Metrics 僅 container
loopback 2000，無 host publish；真連線 readiness 留 D。

## 驗證與回復

`ops/tests/compose_boundary.py` 只接受明確 `STUDYDY_TEST_ENV_FILE`，只列安全摘要。
`ops/tests/nginx_boundary.py` 使用獨立 echo upstream、loopback 4183，驗證 Host、headers
與大小界線；以 `STUDYDY_TEST_FRONTEND_IMAGE`／`STUDYDY_TEST_BACKEND_IMAGE` 指定測試映像。
backend runtime tests 沿用隔離 DB fixture；不得使用正式 `.env` 或資料。

candidate 從 committed SHA 的 `git archive` 建置，記錄 image ID／可用 RepoDigest／
revision label，再核對 runtime ID。最終 main SHA 不同時須重新建置並跑必要驗證。
純本機 build 不宣稱有 registry RepoDigest；不升級 lockfile 或基礎依賴。

C 完成即停止；Cloudflare TLS、真瀏覽器 Secure cookie、Access、外部網路、慢速上傳
與 connector 重連均 **DEFERRED TO D/E**。正式 cutover 前保存前一組 image／設定、
確認資料 mount 與備份。問題發生時先關 route／connector，再回復核准 image，保留
loopback 維護入口；不改 DNS 指向主機、不清正式 volume、不以舊 drift image 公開。
本次沒有 migration；rollback 不自動回復 DB 或 artifact。

官方依據：[cloudflared releases](https://github.com/cloudflare/cloudflared/releases/tag/2026.9.3)、
[token-file](https://developers.cloudflare.com/tunnel/reference/run-parameters/)。

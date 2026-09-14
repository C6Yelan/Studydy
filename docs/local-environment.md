# 本地持久化環境

這是日常產品測試入口。Backend、frontend、PostgreSQL、原始 PDF 都在本機；Gemma 4 在使用者指定的正常 Pod。既有 Unlimited-OCR 使用本機 GPU 與已安裝 runtime。

## 固定位置

以下路徑以 repo 根目錄為起點；開發與測試均使用同一份產品 checkout。

| 用途 | 位置 |
|---|---|
| 操作腳本 | `ops/local/` |
| 前後端來源 | `backend/`、`frontend/` |
| 私密 DB 設定 | `../.studydy-product/private-config.json`，0600 |
| 當前 Pod SSH 入口 | `../.studydy-product/pod-connection.json`，0600；欄位 `ssh_host` |
| 原始 PDF store | `../.studydy-product/artifacts/`，0700 |
| Logs／PID | `../.studydy-product/logs/`、`../.studydy-product/run/` |
| 私人備份 | `../.studydy-product/backups/` |
| 已退役環境資料 | `../.studydy-product/legacy/`；不作啟動入口 |
| OCR 安裝與模型 | `~/.local/share/studydy/ocr/`、`~/.local/share/studydy/models/` |
| 正式 DB container | `studydy-product-postgres` |
| 正式 DB volume | `studydy-product-pg18`；持久化掛載，不使用 tmpfs／auto-remove |

資料不跟著 checkout、backend restart 或 Pod 停止而刪除。禁止刪除 volume、清空 PDF store，或拿 disposable preview/test DB 替代這個 product DB。私密設定、Pod 識別資訊、DSN、API key、教材與 logs 不加入 Git，也不要輸出內容。

## 啟動與停止

```bash
python3 ops/local/manage.py status
python3 ops/local/manage.py start
```

開啟 <http://127.0.0.1:4173>。Backend 在本機 8001，SSH 模型通道在本機 18000，資料庫只對本機 55432 開放。Local profile 使用相同 origin 與 HttpOnly session cookie。

`start` 只啟動既有持久 DB、本機按需模型通道、正式 backend、前端 production build。Backend 啟動只驗證 runtime 設定，不要求 Pod／模型在線；處理教材與出題仍在實際操作時驗證 AI runtime，離線時回報既有錯誤。SSH 通道收到 AI 請求才連線，失敗不自動重播請求；之後的新請求可重新連線。不自動跑教材 smoke。已有程序會沿用，未知程序占用 port 時拒絕重複啟動。若報告 still starting，先查 status／log，不要反覆 start 或重送模型請求。

```bash
python3 ops/local/manage.py stop
```

`stop` 只停止這組本機 frontend／backend／SSH 通道，不刪資料、不停止 PostgreSQL 或 Pod。要關掉 AI 費用，須另行明確停止 Pod；關閉 SSH 或 HTTP client 不代表遠端推論已取消。

## 換正常 Pod

1. 使用新的 SSH 入口更新私密 `pod-connection.json`；不要把實際值寫進 repo。
2. 透過 SSH 等待 vLLM 初始化完成。模型、revision、context 依 `local_ai/runtime-lock.json`。
3. 重啟本機服務以使用新的通道；DB、PDF store 和 private-config 保留。

現有 SSH 通道僅轉送固定模型路由。Server key 只在 Pod 端從 `VLLM_API_KEY` 讀取，不複製到本機。通道不自動重播失敗請求。這是部署連線工具，並非第二條 semantic pipeline；產品仍呼叫相同 HTTP semantic service。

新對話先讀本文件與 `status`，不要從歷史實驗目錄找舊 launcher、查 Serverless endpoint 或擅自排入模型測試。執行真實推論前要遵守當次使用者指定的 request 數量與預算。

## 目前 candidate 的資料契約

Runtime lock v16／binding v1 採先前批准的 clean cutover。既有 product DB 的資料不會被啟動腳本改寫；舊模型產生的開發 Map 需要重新生成，不能把保存資料與目前版本可 reopen 混為一談。不要修改 DB hash 或加舊版 reader 來繞過驗證。

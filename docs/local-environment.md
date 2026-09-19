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

B3-A／B3-B candidate 使用 runtime lock v18，保留來源 scope／增量 review flag 契約並修正 command 分批；模型與 server pin 保持原設定，開發替代模型仍由 private config 注入。2026-09-19 經使用者授權，產品 DB 已備份並升級 migration 0009；同日完成 B3-B 後已重新載入服務供整體驗收，B3-B 無新增 migration。13 張既有資料表的原有欄位雜湊核對一致；啟動未呼叫模型。已保存 KS／題目／答案不改寫 hash，來源、學習契約與備份紀錄見 [B3-A／B3-B](source-revisions.md)。

同日依使用者要求，已直接移除私人 command 設定中的 `timeout_seconds`、`max_input_bytes`、`max_calls` 與執行器的相應檢查，未以 `null` 或其他數值替代。原設定留在 private backups，服務已重新載入；本次沒有模型呼叫。設定格式見 [開發替代模型](document-normalization.md#開發替代模型)。

後續已修正「取消限制同時跳過分批」的錯誤：command 重新共用連續 Evidence 分批器，估算每批新增內容，並攜帶累積概念。這是批次安排，不是重新限制教材總量；CLI 的估算不能宣稱與 Gemma tokenizer 的批次完全相同。v18 服務已重新載入，無模型重跑。

目前服務另已載入分析保存／失敗接續與 canonical 合併修正。教材的模型原始產物和每批 checkpoint 保存在既有 artifact root 的 `analysis/{learner}/{material}/{run}/`，不隨成功／失敗自動清除；只有明確刪除教材才清除。來源與 runtime 相同的重試可沿用已完成批次，若只剩組裝則不再需要模型。舊失敗作業若當時沒有保存資料，無法追補；不會假標成功。無新增 DB migration，也沒有重跑使用者模型工作。

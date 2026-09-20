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

## 目前日常服務與資料契約

2026-09-20 最新已修正跨觀念封鎖：未完成 A 不妨礙進入或建立 B 的題組，各自可接續。持久 DB 備份後升級 migration 0013，唯一索引只限制同一 StudySession／Concept，15 張既有表的原有欄位不變。日常前後端已重載，題組列表為 v3；既有題目與作答保留，沒有模型重跑。詳見[觀念切換契約](assessment-sets.md)。

2026-09-20 較早依使用者要求，測驗改為[整組一次交卷](assessment-sets.md)，並統一單欄題卡、桌機雙欄選項與手機單欄選項。原子保存完整答案後一起顯示結果，沒有逐題送出按鈕。該次前後端重新載入時 schema 維持 0012，無模型呼叫、migration 或原作答改寫；目前 schema 以上述 0013 為準。切換前已保存的答案可連同剩餘題目完成交卷，不重複計分。

2026-09-20 較早已載入 [B5-R 錯題補強](assessment-remediation.md)。持久 DB 備份後升級 migration 0012，15 張既有表的原有欄位核對一致；`manage.py stop/start` 已載入題組 v2、進度／恢復 v3。舊題組保持原狀，複習後可只對錯誤重點建立補強；補強答對不計入獨立掌握證據。此次切換未呼叫模型或改寫產品成績，Luna 八次合成教材驗證在隔離 DB 完成。

2026-09-20 後續已載入 [B5-D 單一觀念題組](assessment-sets.md) 與 heading 誤分類選題修正，DB 已備份並升級 migration 0011；升級時 13 張既有表的原有欄位全部核對一致。新出題入口一次準備單一觀念的多重點題組，既有題目／作答仍可讀取。使用者另明確授權的一輪私人教材出題用了 12 次 Luna 呼叫，原訂六題全部發布，未代答；品質仍有編號提示與偏記憶題限制，未視為完整理解驗收。日常前後端與持久資料位置不變。

2026-09-20 已以 `manage.py stop/start` 載入 B5-Q、教材／出題設定解耦與 checkpoint 清理。日常前後端仍為 4173／8001，持久資料庫已備份並升級到 migration 0010。既有四個可用原 hash 核對的工作補入執行設定，所有原有欄位在升級前後一致；沒有新增 legacy 執行分支。教材庫與既有地圖已在原登入狀態下實際讀取驗證，瀏覽器沒有 console 錯誤。

B3-A／B3-B candidate 使用 runtime lock v18，保留來源 scope／增量 review flag 契約並修正 command 分批；模型與 server pin 保持原設定，開發替代模型仍由 private config 注入。2026-09-19 經使用者授權，產品 DB 已備份並升級 migration 0009；同日完成 B3-B 後已重新載入服務供整體驗收，B3-B 無新增 migration。13 張既有資料表的原有欄位雜湊核對一致；啟動未呼叫模型。已保存 KS／題目／答案不改寫 hash，來源、學習契約與備份紀錄見 [B3-A／B3-B](source-revisions.md)。

同日依使用者要求，已直接移除私人 command 設定中的 `timeout_seconds`、`max_input_bytes`、`max_calls` 與執行器的相應檢查，未以 `null` 或其他數值替代。原設定留在 private backups，服務已重新載入；本次沒有模型呼叫。設定格式見 [開發替代模型](document-normalization.md#開發替代模型)。

後續已修正「取消限制同時跳過分批」的錯誤：command 重新共用連續 Evidence 分批器，估算每批新增內容，並攜帶累積概念。這是批次安排，不是重新限制教材總量；CLI 的估算不能宣稱與 Gemma tokenizer 的批次完全相同。v18 服務已重新載入，無模型重跑。

教材的模型原始查核產物與未完成 checkpoint 位於既有 artifact root 的 `analysis/{learner}/{material}/{run}/`。地圖發布完成後清理 checkpoint，即使品質仍是 `needs_review` 也一樣；尚未完成的分析保留接續狀態。其餘私人呼叫資料只在明確刪除教材時清理。來源與教材分析設定相同的重試可沿用已完成批次，若只剩組裝則不再需要模型；只改出題設定不影響接續。當時沒有保存資料的失敗作業仍無法追補，不會假標成功。

本次切換已清理兩份已發布 checkpoint，以及一份相同輸入／分析設定、已被後續發布涵蓋的失敗 checkpoint；原 run 狀態、教材、地圖與作答資料保留。切換未呼叫模型。備份與逐表核對紀錄位於私人 `../.studydy-product/experiments/b05-finish-20260920/`。詳見 [現行 checkpoint 契約](source-revisions.md#分批保存與失敗重試)。

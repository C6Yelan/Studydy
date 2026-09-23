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

2026-09-23 移除來源轉檔在政策變動時建立第二筆 job、讀取端選最新 job 的開發相容路徑。
現行同一來源只能有一筆 normalization；失敗重試只重設該筆狀態，不改寫保存的政策，
政策不符時轉檔如實失敗。切換前 9 筆來源與 9 筆 normalization 一一對應、無活動工作；
產品 DB 備份後完成交易乾跑與回滾，再新增唯一約束並接軌 0002 checksum。15 張產品表
資料摘要未變，runner 重跑回傳 `()`，服務已恢復。隔離來源轉檔測試 17 項通過。
私人備份與核對紀錄：`../.studydy-product/backups/normalization-single-job-20260923T124935Z/`。

2026-09-23 收緊現行 baseline 的資料約束：教材名稱必填；ready 轉檔必須同時保存 normalized PDF、
mapping 與頁數，其他狀態不得帶有這些輸出；教材處理工作必須綁定 SourceSet、bundle manifest
及啟動時的 runtime lock snapshot。已移除舊工作可全空的 bundle 分支。切換前確認 3 筆教材、
9 筆 ready 轉檔均符合新規則，處理工作表為空。完整非 browser 後端回歸 380 項、
教材庫與轉檔的隔離 DB 瀏覽器案例 2 項通過；
產品 DB 備份後完成交易乾跑與回滾，再套用 schema 並接軌 0001／0002 checksum。
15 張產品表資料摘要未變，runner 重跑回傳 `()`，服務已恢復。私人備份與核對紀錄位於
`../.studydy-product/backups/schema-content-tightening-20260923T080242Z/`。

2026-09-23 後續修正 v1 切換後保留的 3 筆教材草稿重播：原 `upload_request_fingerprint`
仍由舊版建立名稱公式計算，與現行 v1 請求不一致。逐筆核對唯一原建立名稱、完成私人 DB 備份後，
只更新這 3 筆 fingerprint；同一交易內確認教材其他欄位不變。服務已恢復，原始來源、檔案、
帳號及其餘資料未改動。回復快照與核對紀錄位於
`../.studydy-product/backups/draft-fingerprint-v1-20260923T073724Z/`。

2026-09-23 已完成 **Studydy 自有契約統一 v1**。以各契約最新資料形狀為準，
backend、frontend、API、runtime lock、來源／學習／出題與持久 schema 使用同一版本；
第三方 API、套件、模型 revision 與題組樂觀鎖計數不更動。所有產品路由位於 `/v1/`，
原檔下載為 `/v1/artifacts/{id}/download`，normalized PDF 預覽為 `/v1/artifacts/{id}`。
兩個入口保留 owner、artifact 類型、原 MIME／下載檔名及 PDF／ETag 的獨立契約；不提供舊路由 alias。

Knowledge Structure builder 產生沒有正式 schema／revision 的內部草稿；來源綁定完成後才建立
`knowledge-structure/v1` 並計算 revision。持久 reader 與 DB 約束只接受此正式契約，
包含拒絕缺少 schema 的 JSON；不把舊資料改標籤後沿用 revision。HTTP／command runtime binding
共用 v1 envelope，依現行 transport 區分；其他多版本接受與舊版判斷已移除。

經使用者授權，先備份並隔離演練後，在停止寫入期間清理以下衍生紀錄：

| 衍生資料 | 清理筆數 |
|---|---:|
| 地圖／處理作業 | 3／6 |
| 學習 session／題目／作答 | 1／24／22 |
| 題組／題組項目 | 7／24 |
| 來源集合快照／快照項目 | 3／9 |

保留 3 筆教材、9 筆來源、9 筆正規化、27 個 artifact、全部實體檔案、帳號與登入 session。
教材僅清除 head revision；正規化僅把自有 policy.version 從 3 遷移為 1，其他欄位保持不變。
轉檔器、模型與檔案 bytes 不變；舊來源集合快照已清理，之後建立新 run 時會重新計算 policy／
manifest 摘要。此 metadata 遷移在同一交易內暫停 ready immutability trigger，完成後恢復並核對。
其餘保留紀錄逐列摘要與全部實體檔案雜湊已核對一致。

新版 `0002_sources_and_processing.sql` 的 checksum 已受控採認，其他三份帳本不變。
新建 schema、隔離清理／回滾、產品切換及重跑 `()` 均通過。後端 381 項（全套 380 通過後，
修正最後一個舊路由斷言並單獨重驗通過）、local AI 13 項、前端 6 項 Node 測試、TypeScript／
production build 通過；4 個真 API／隔離 DB browser fixtures 涵蓋轉檔、多來源發布、交卷與補強。
全部使用受控模型 fixture，沒有真實模型呼叫或品質驗收。

切換後唯讀驗證 3 筆教材均無 head／作業／學習紀錄，9 筆來源 ready 且可供重新分析，
27 個 artifact 都可依 owner 驗證讀取；所有衍生表空白。首頁 200、未登入 `/v1/materials` 401、
舊 `/v2/materials` 404，前後端已重載。使用者之後可從保留來源重新開始分析，本次未自動啟動。

私人備份與證據：`../.studydy-product/backups/contracts-v1-20260923T070420Z/`。
包含 `product.dump`、切換前最新 `product-before-apply.dump`、原 migration／runtime lock、
`rehearsal.json`、`applied.json`、`files-before.json` 與 `product-verification.json`。
恢復舊備份須搭配備份中的原契約程式／SQL，不可混用新版帳本與舊 artifact 契約。

## 歷史切換紀錄

以下版本號與測試數字屬於各次切換當時的基準；現行契約以上方 v1 說明為準。

2026-09-23 後續經使用者明確授權，已完成 **pdf-v1 退役清理及來源集合模型切換**。
`ingestion_kind` 欄位、舊 PDF 必填約束、`source_pdf` artifact 類型及唯一索引已移除；
ORM、查詢、API 與前端型別也移除無用途的標記。`source_artifact_id` 與現行 PDF 來源集合流程保留。
產品 DB 的新版 0001 checksum 已受控採認，0002～0004 帳本不變，前後端已重新載入。

已授權並執行的清理範圍為：

| 資料 | 筆數 |
|---|---:|
| `pdf-v1` 教材／`source_pdf` artifact | 各 7 |
| 原始來源／正規化紀錄 | 各 7 |
| 處理作業／知識結構 | 8／6 |
| 學習 session／題目／作答 | 4／20／19 |
| 題組／題組項目 | 1／4 |
| 來源集合／集合項目 | 0／0 |

實際方式：保留原 store 全部檔案，另外保存 7 份可重新上傳的 PDF 及私人檔名／owner／ID／SHA
對照清單。完整 DB 備份已還原到隔離 PostgreSQL；先演練再以 SQL 清理上述教材的相依 DB 紀錄，
保留帳號與登入 session，不呼叫 `purge_discarded_material()`、quarantine、reconcile 或分析檔清理。
交易內先解除目標 head 關聯，再依 FK 順序刪除學習、處理與來源紀錄；結清延後 FK 檢查後，
移除 `ingestion_kind`、PDF 必填約束及舊 artifact 唯一索引，收窄 artifact role 約束。
新舊 schema 核對（function 定義忽略純空白排版差異）與保留資料逐列摘要一致後，才採認新版
0001 checksum；0002～0004 帳本保持原樣。產品切換前重新核對相同目標與零活動工作，停止服務並更新完整備份；單一交易完成清理與採認，
其餘產品紀錄（含帳號與登入 session）逐列摘要及全部 artifact 檔案前後一致。
新版後端非 browser 回歸 376 passed，前端 6 項 Node 測試與 TypeScript 檢查通過。

私人備份：`../.studydy-product/backups/pdf-v1-retirement-20260923T063510Z/`。
內含 `product.dump`、`previous-migrations/`、`original-pdfs/`、`manifest.json`、候選清理 SQL
及隔離演練結果。原 store 的 7 份 PDF 與額外副本皆已核對雜湊。未來重新分析須由使用者重新
上傳／啟動，不自動呼叫模型。切換前最新備份為 `product-before-apply.dump`；正式執行及讀取
核對見 `applied.json`、`product-read-verification.json`。

切換後教材庫、3 份保留地圖與 3 組 Evidence 來源、7 個保留題組及題組列表均可讀，
API response models 驗證通過，唯讀檢查未修改資料。Migration 重跑回傳 `()`；首頁 200、
未登入 session 401，服務已恢復。未重算來源、地圖、題目或答案，沒有真實模型呼叫。

2026-09-23 已將發布前的 14 份 SQL 收斂為四份領域 baseline，持久 DB 帳本已接軌到新的 0001～0004：

| Migration | 領域 |
|---|---|
| `0001_identity_and_materials.sql` | 帳號、登入、教材與 artifact |
| `0002_sources_and_processing.sql` | 來源、正規化、處理工作與知識結構 |
| `0003_learning_and_answers.sql` | 學習 session、題目與作答 |
| `0004_assessment_sets.sql` | 初篩／補強題組與不可變約束 |

新 SQL 直接建立最終結構；runner 仍核對從 0001 連續的版本與 SHA-256，後續變更從 0005 接續。
原 14 份 SQL 從有效目錄移除，歷史由 Git 與本次私人備份保留。下方較早紀錄的版本號均屬舊基準。
其他尚未接軌的舊 DB 不可直接執行新 SQL，也不可只替換 checksum 來略過驗證。

切換前已確認舊 0014 帳本與全部 checksum，沒有待處理模型工作；新舊 schema 的欄位、約束、
索引、function 與 trigger 完全一致。停止服務後完成 DB 備份、隔離還原、帳本採認與回滾演練，
才以單一交易替換產品 migration 帳本。15 張產品資料表逐列摘要及全部 artifact 檔案前後一致；
跨資料庫摘要固定使用 C 排序，避免產品與測試 DB 的 collation 差異造成誤判。
新 runner 重跑回傳 `()`，既有前後端已恢復，首頁 200、未登入 session 401 符合設定。
未呼叫真實模型，未改寫帳號、登入 session、教材、地圖、答案或來源 hash。

驗證為後端非 browser 回歸 374 passed，另新增補強歸屬不可變測試 1 passed。
當時教材庫及 3 份 head 地圖／Evidence 可讀，另 6 份舊 head 地圖未通過 reader 驗證；
後者已在上述 pdf-v1 退役授權中清理。前次題組檢查腳本誤以 UUID 呼叫要求 `TrustedLearner` 的
介面，因此「8 個題組／2 份列表失敗」的結論無效；本次用正確參數驗證保留的 7 個題組及列表
全部通過。這是修正驗證紀錄，未繞過產品驗證或重算內容。

私人備份與核對證據：`../.studydy-product/backups/baseline-cutover-20260923T060119Z/`，
包含 `product.dump`、新舊 SQL、舊帳本、`verification.json` 與 `reader-verification.json`。
還原舊備份時，須搭配同份備份的舊 SQL／舊帳本；不要混用新 baseline。產品原始檔保持原位置。

2026-09-23 後續只整理四份 baseline SQL 的排版。修改前後 SQL 詞彙一致，隔離 migration 測試通過；
停止本機服務後，在單一交易更新產品帳本的四筆 checksum，runner 重跑回傳 `()`，服務已恢復。
排版前的 SQL 與帳本保存於 `../.studydy-product/backups/migration-readability-20260923T061914Z/`；
該快照用於回復此次排版，不能與上方更早的 14 版備份混用。

2026-09-21 後續依使用者要求，已移除以頁碼及職稱／機構關鍵字放行署名的例外規則，
連同專屬測試與功能說明一併刪除。31 項檢核測試通過，本機服務已重載；沒有模型呼叫，
已發布教材及作答未重算。

2026-09-21 已載入 runtime lock v20 的[教材檢核與整理](material-review.md)：初始分析後由同一 worker
檢核整合，再發布新 KS；既有多檔教材可重用已保存 Evidence 重新整理。沒有 migration，原 KS、題目及答案保留。
本輪已在日常服務執行真實教材整理及兩組初篩／錯題補強，確認來源回查、交卷恢復及重新登入接續。
實測仍發現多解題被出題檢核接受並計入掌握證據，因此本輪品質未通過，不能把地圖節點減少當成準確率提升。
本輪使用已授權的 command 模型；未執行 Gemma／Pod 品質驗收。私人證據位於工作區
`../.studydy-product/experiments/b06-applied-20260921/`。

2026-09-20 最新已修正跨觀念封鎖：未完成 A 不妨礙進入或建立 B 的題組，各自可接續。持久 DB 備份後升級 migration 0013，唯一索引只限制同一 StudySession／Concept，15 張既有表的原有欄位不變。日常前後端已重載，題組列表為 v3；既有題目與作答保留，沒有模型重跑。詳見[觀念切換契約](assessment-sets.md)。

2026-09-20 較早依使用者要求，測驗改為[整組一次交卷](assessment-sets.md)，並統一單欄題卡、桌機雙欄選項與手機單欄選項。原子保存完整答案後一起顯示結果，沒有逐題送出按鈕。該次前後端重新載入時 schema 維持舊 0012，無模型呼叫、migration 或原作答改寫；同日後續升至舊 0014。切換前已保存的答案可連同剩餘題目完成交卷，不重複計分。

B05-R 目前採用[直接錯題補強](assessment-remediation.md)：初篩交卷後閱讀錯誤重點與來源，直接建立補強，結果由 AnswerEvent 推導。契約為題組 v3、進度 v4、resume v5，資料結構已納入領域 baseline 0004；補強作答仍不計入獨立掌握證據。

2026-09-20 後續已載入 [B5-D 單一觀念題組](assessment-sets.md) 與 heading 誤分類選題修正，DB 已備份並升級 migration 0011；升級時 13 張既有表的原有欄位全部核對一致。新出題入口一次準備單一觀念的多重點題組，既有題目／作答仍可讀取。使用者另明確授權的一輪私人教材出題用了 12 次 Luna 呼叫，原訂六題全部發布，未代答；品質仍有編號提示與偏記憶題限制，未視為完整理解驗收。日常前後端與持久資料位置不變。

2026-09-20 已以 `manage.py stop/start` 載入 B5-Q、教材／出題設定解耦與 checkpoint 清理。日常前後端仍為 4173／8001，持久資料庫已備份並升級到 migration 0010。既有四個可用原 hash 核對的工作補入執行設定，所有原有欄位在升級前後一致；沒有新增 legacy 執行分支。教材庫與既有地圖已在原登入狀態下實際讀取驗證，瀏覽器沒有 console 錯誤。

B3-A／B3-B candidate 使用 runtime lock v18，保留來源 scope／增量 review flag 契約並修正 command 分批；模型與 server pin 保持原設定，開發替代模型仍由 private config 注入。2026-09-19 經使用者授權，產品 DB 已備份並升級 migration 0009；同日完成 B3-B 後已重新載入服務供整體驗收，B3-B 無新增 migration。13 張既有資料表的原有欄位雜湊核對一致；啟動未呼叫模型。已保存 KS／題目／答案不改寫 hash，來源、學習契約與備份紀錄見 [B3-A／B3-B](source-revisions.md)。

同日依使用者要求，已直接移除私人 command 設定中的 `timeout_seconds`、`max_input_bytes`、`max_calls` 與執行器的相應檢查，未以 `null` 或其他數值替代。原設定留在 private backups，服務已重新載入；本次沒有模型呼叫。設定格式見 [開發替代模型](document-normalization.md#開發替代模型)。

後續已修正「取消限制同時跳過分批」的錯誤：command 重新共用連續 Evidence 分批器，估算每批新增內容，並攜帶累積概念。這是批次安排，不是重新限制教材總量；CLI 的估算不能宣稱與 Gemma tokenizer 的批次完全相同。v18 服務已重新載入，無模型重跑。

教材的模型原始查核產物與未完成 checkpoint 位於既有 artifact root 的 `analysis/{learner}/{material}/{run}/`。地圖發布完成後清理 checkpoint，即使品質仍是 `needs_review` 也一樣；尚未完成的分析保留接續狀態。其餘私人呼叫資料只在明確刪除教材時清理。來源與教材分析設定相同的重試可沿用已完成批次，若只剩組裝則不再需要模型；只改出題設定不影響接續。當時沒有保存資料的失敗作業仍無法追補，不會假標成功。

本次切換已清理兩份已發布 checkpoint，以及一份相同輸入／分析設定、已被後續發布涵蓋的失敗 checkpoint；原 run 狀態、教材、地圖與作答資料保留。切換未呼叫模型。備份與逐表核對紀錄位於私人 `../.studydy-product/experiments/b05-finish-20260920/`。詳見 [現行 checkpoint 契約](source-revisions.md#分批保存與失敗重試)。

# 觀念圖卡與我的圖卡組（F4）

本輪從 `be/feature-concept-cards` 的 `4c0e87f39992a51f93425690a379b54c134098c0` 延續實作。
V1 的 source-bound projection 與 exact source resolver 保留；使用介面改為持久卡組與單張翻卡。
本輪未部署 production、未在 production DB 套 migration，亦未修改競賽 checkout。

## 使用流程

教材庫每份已發布教材的「觀念圖卡」，或知識地圖的快捷入口，進入該教材的「我的圖卡組」。

1. 建立圖卡組：輸入名稱、搜尋並勾選 concepts、查看已選數量，依教材發布順序儲存。
2. 複習：一次一張。正面以概念名稱為焦點，點擊／Enter 翻面後才顯示 canonical claims。
3. 可上一張、下一張、重新開始、完成複習或返回卡組清單；再次開啟／重新整理會回到保存的位置，從正面開始。
4. 可重新命名、修改選取概念及刪除卡組。改名保留位置；選取內容改變時回到第一張。

卡片不呈現 relation graph、先備清單、related concepts 或比較表。
「查看教材來源」另開來源 dialog，提供每項重點的 exact Evidence 節錄／識別及既有 PDF／原檔 resolver。
needs_review 以「此教材有內容待確認」提示，原始 reason codes、decision 與排除頁留在可展開的品質詳情。
文字以原文渲染；程式碼區可水平捲動，長內容在卡片背面的內容區捲動，不刪減 claim 或改寫 literal。

## Persistence 與知識邊界

新增 additive migration：`backend/migrations/0006_card_sets.sql`。不改寫既有資料表、KS 或資料列。

| Table | 保存內容 |
| --- | --- |
| `card_sets` | owner、material UUID、exact KS revision、名稱、`published_order`、current_position、version、建立／更新時間、create idempotency key digest／request fingerprint |
| `card_set_items` | card_set_id、0-based position、concept_id；同卡組 concept 與 position 唯一 |

沒有複製 claim／relation／Evidence 文字、HTML 或 KS document。
每次讀卡使用 saved references → authorized exact KS → 既有 deterministic projection。
既有 `concept-cards/v1` stateless API 及 relation projection 保留，新的 CardSet identity 不保存 relation IDs；flashcard renderer 不顯示 relations。

`current_position` 只是續讀位置，不是學習評量。翻面只改瀏覽器狀態；前後張／重新開始只修改卡組位置。
完成複習回到清單，保留最後位置，不記錄通過、熟悉度或 mastery。沒有新的 StudySession、answer event、評量事件、AI 或排程引擎。

## Retention 與生命週期

- CardSet 的 `(learner_id, material_id, knowledge_structure_revision)` FK 指向正式 KS；沒有 fake StudySession。
- `_prune_unreferenced_structures` 同時檢查 StudySession、active run 與 CardSet。
- 卡組建立／編輯／刪除與 publisher／prune／教材刪除共用 material row lock；建立與 prune 競態只會成功保留完整引用，或因版本已不可用而拒絕建立。
- 刪除卡組會 cascade 刪除 items；若無其他引用，舊 KS 在下一次正常 prune 重新具備清理資格。head 與其他既有引用仍受原規則保護。
- 教材刪除意圖成立後，卡組讀寫立即拒絕；purge 在清理 KS 前刪除 CardSets，教材刪除具有最高優先權。
- 已保存卡組維持原 revision，不自動換成 head；編輯也不能更換 revision。

## API 與並行操作

共同 scope：`/v1/materials/{material_id}/card-sets`。

| Method / suffix | 行為 |
| --- | --- |
| GET collection | 列出自己的教材卡組，按 updated_at／ID 排序 |
| POST collection | 建立；需要 Idempotency-Key |
| GET `/{card_set_id}` | 讀取 metadata 與有序 concept IDs |
| GET `/{card_set_id}/cards` | 讀取 metadata 及 exact canonical cards |
| POST `/{card_set_id}/edit` | 名稱／concepts／policy，附 expected_version 與原 revision |
| POST `/{card_set_id}/position` | 保存合法範圍的位置，附 expected_version |
| DELETE `/{card_set_id}?version=...` | 以版本檢查刪除；空 body |

Owner 一律由 session 決定，不接受 body learner_id。沿用 exact Origin、private no-store 與固定錯誤回應。
錯 owner／material／不存在卡組回 404；無效 concepts 或輸入回 400；revision、create intent 或版本衝突回 409。
Create 輸入先按 canonical 順序去重，重送相同 intent 不建立第二組。
Edit 重送已成立的相同內容可接回；不同目標不能覆蓋較新版本。位置更新先核對版本，以免舊頁覆蓋編輯後的新序列。
前端遇到衝突會要求重新讀取；換頁、帳號 invalidation 與晚到 create/read 回應不得導向或覆蓋新頁。

## 驗證結果

以 `docs/testing.md` 的 disposable PostgreSQL、合成 fixtures 與專用 4183／8002 ports 執行；沒有使用 production 資料或模型。

| 範圍 | 結果 |
| --- | --- |
| Domain／既有 deterministic projection | 8 passed |
| Migration framework（含重跑／交易回滾／並行安裝） | 7 passed |
| CardSet storage、v5→v6、CRUD／resume／retention／競態 | 6 passed |
| CardSet HTTP lifecycle／owner／revision／conflict | 1 passed |
| 既有 F4 HTTP regression | 3 passed |
| 真 publisher CardSet retention，加上既有 retention cases | 3 passed |
| 既有 material full-delete／artifact regression | 12 passed |
| 真 API／DB／browser 卡組端到端 | 1 passed（內含 1 Playwright case） |
| Node tests | 6 個 test files passed；client 單獨執行 33 cases passed |
| TypeScript／production frontend build | passed |
| F4、地圖、教材庫、shell mock browser | 61 cases 分組通過（37＋24）；F4 本身 8 cases |
| Git diff whitespace check | passed |

後端非 browser 共 40 個不同 cases 通過，不重複計算各次重跑。
Browser 覆蓋建立／搜尋／單選與多選／編輯／刪除、翻面、前後張、進度、restart、reload/reopen、來源、品質詳情、長 literal／一般文字、失敗恢復、帳號與晚到回應。
1440px／390px 翻卡截圖已檢視；原有教材庫的幾何與溢位 assertions 保留並通過。Chromium emulation 不代表手機實機或 Safari 驗收。

真 API browser 測試前後，`product_snapshot` 的 KS、StudySession、assessments、answer_events 等完整 row 摘要相同，existing LearnerProgress 也逐值相同。
只允許 CardSet endpoints 產生非登入寫入，並封鎖 backend model HTTP；無 session 與有 progress 兩種教材都驗證。
不是以 screenshot snapshot 取代 provenance：cards 與 exact KS concepts 逐值比較，source URL revision／Evidence ID 及實際 PDF 回應皆有 assertions。

建置有非阻擋性的 Vite bundle-size 提示；TestClient 有既有 httpx 棄用警告。

## 部署與延後項目

本輪僅實作、isolated migration 驗證、commit／push，等待另行授權部署。
下一次部署需要支援 migration 0006 的 backend；目前框架會拒絕比自身檔案更新的 migration ledger，因此套用 0006 後不能直接以 V1 舊 image 啟動作為 rollback。不要刪除 ledger 或對 production 執行降版；應保留新 schema 並使用相容 image。

已完成 deterministic published order 與位置續讀。Shuffle、每次張數、swipe、跨教材卡組、分享／匯出、自動選材與 spaced repetition 未實作。
AI／Pod／Gemma 不是 dependency；未新增 mastery 或第二套 knowledge authority。

# 取消並刪除教材

Upload → Processing 是新教材的第一次分析。「取消並刪除教材」會停止不需要的分析，並刪除該份 Material、處理紀錄及原始 PDF。處理失敗本身不代表刪除授權：failed、legacy cancelled 或尚未分析的教材仍保留，由 learner 確認「刪除教材」。

## Canonical contracts

- 整份教材刪除入口：`DELETE /v1/materials/{material_id}`。
- HTTP 202、`material-discard/v1`：`material_id` 與 `state: removing | removed`。
- CookieSession、Origin required；拒絕 query、非空 body 與 client learner override，不需要 Idempotency-Key。
- 跨 owner／已不存在為 `RESOURCE_NOT_FOUND` / 404。不保存刪除 tombstone；重複 DELETE 在 removing 期間穩定，完成後為 404。
- 已要求刪除的教材不可 rename 或 create run（`MATERIAL_NOT_DISCARDABLE` / 409）；DB／filesystem 暫時錯誤為 `STORAGE_UNAVAILABLE` / 503。
- 單 PDF run 為 `material-processing-run/v5`；來源集合 run 為 v6，output binding 維持 v4。
- B3-A 追加使用 `POST /v2/material-processing-runs/{run_id}/cancel`，保留目前地圖與學習紀錄，詳見 [追加契約](source-revisions.md)。首次分析的「取消並刪除教材」仍使用 DELETE。

## Eligibility 與持久化意圖

| Material 的全部資料 | DELETE 行為 |
|---|---|
| 無 run | 直接刪除 |
| 全部 run 都 failed/cancelled | 直接刪除 |
| Pending | 保存 discard intent，pending 立即 cancelled，再刪除 |
| Running queued/evidence/semantics | 保存 intent，對所有 active runs 接受 cancellation，回 removing |
| Succeeded/partial、KnowledgeStructure、StudySession、Assessment、AnswerEvent | 保存 intent 後清除這份教材全部衍生資料 |
| Running publishing | 保存 intent；尚未提交的 publication 必須停止，worker 收斂 terminal 後 purge |

`materials.discard_requested_at` 是 nullable internal authority。整份刪除需持有 Material → run locks 並保存 discard intent；B3-A 的 run-only cancel 另驗證 owner、run 與 base revision，不建立整份刪除意圖。新 run 拒絕已要求 discard 的教材。

Run statuses 仍只有 pending、running、succeeded、partial、failed、cancelled。既有 `cancel_requested_at` 無論是否有 Material intent，worker 都必須 honor；歷史 cancelled 資料合法且不會被 migration 補 intent 或自動刪除。

## Worker 與 race

Discard 先鎖 Material，再依 run ID 鎖住全部 runs，等待全部 active runs 安全結束。取消與 publishing transition 使用同一 run row lock：

- discard 先：Material intent 和所有 cancellation requests 同一 transaction commit；publishing checkpoint honor cancellation，不發布新 structure。
- publication 已先提交：保留該次 terminal 結果，再清除整份教材；僅進入 publishing 階段尚不代表提交，刪除意圖可阻止晚到發布。
- 已接受取消先於 failure：terminal 是 cancelled、error_code null。Failure 已先完成則不改寫其結果；明確 discard 仍可清除該份教材。
- Runtime work 前、preflight 後、evidence page、semantic bundle、下一個 bundle/retry、publishing 前維持 cooperative checkpoints。單一已在執行的 OCR/Gemma request 允許先完成。
- Cancellation terminal transaction 先 commit、pipeline 正常 unwind；worker 再呼叫統一 purge authority。多個 run 必須全部停止才可 purge。
- Startup 先恢復 interrupted runs，再 reconciliation/purge。Worker 完成工作與正常輪詢時重試尚未完成的 discard；沒有另一張 queue 或 scheduler。

## DB 與實體 PDF 清理

`runtime/material_discard.py` 是唯一 eligibility / deletion SQL authority。`runtime/storage/artifacts.py` 負責檔案操作：

1. 鎖 Material、全部 runs、KnowledgeStructures、StudySessions 與 source Artifact，重新確認沒有 active run。
2. 原子 rename `objects/<artifact_id.hex>` 到同一 artifact root 的私有 `.trash/`，同步目錄。
3. 同一 DB transaction 明確依序刪除 AnswerEvents → Assessments → StudySessions → KnowledgeStructures → runs → Artifact → Material；不增加 cascade。
4. Commit 後由新 transaction 查 DB：沒有 Artifact reference 則 unlink quarantine PDF。
5. Rollback／commit acknowledgement 遺失時，也重新查 DB 決定 restore 或 unlink，不猜測 commit 是否成功。
6. Process crash 留下的 quarantine 在 startup/worker retry reconciliation：DB 仍引用 → restore；不再引用 → unlink。

每個 source 的 PostgreSQL advisory transaction lock 讓 reconciliation 等待 rename 所屬 DB transaction 完成。Quarantine 0700、不對產品提供 URL；清理失敗保留可重試的私有檔案，不建立指向已永久刪除 PDF 的 DB row。若 filesystem/DB 持續不可用，清理需等 storage 恢復；不會回報成功刪除。已提交後 unlink 失敗可能回 503，此時 DB row 已刪除，worker/startup 仍會清除 quarantine。

## Frontend

Processing 以 inline confirmation 送出一次 DELETE。`removed` 直接返回我的教材；`removing` 顯示「正在取消並刪除教材」，GET polling 繼續。新 invariant 使 reload 讀到 active cancellation request 時可重建刪除狀態。

只有本頁已收到 DELETE acceptance 或讀到 active persisted cancellation intent，後續 run 404 才當成刪除完成返回教材集合；一般 404 仍顯示讀取錯誤。若直接重開已刪除的舊 URL，沒有 acceptance 證據時也維持一般 404，不推測刪除原因。

Failed/cancelled/no-run card 只有在沒有 map/session 時呈現「刪除教材」，使用 inline confirmation、保留／確認按鈕、鍵盤焦點與 Escape。DELETE 失敗保留卡片並提供 retry；成功更新列表與數量。Server 仍以全部 runs 決定 eligibility，不信任 latest_attempt gating。Detail 沿用同一小型控制元件，沒有重設版面。

## Migration 與版本切換

新增 `0006_material_discard.sql`，只新增 nullable Material 欄位；0001–0005 完全不變。升級前確認舊版沒有 active cancel-only request，並協調停止舊 backend，再 migration/build/start；不要讓舊 public cancel route 與新 invariant 混用。歷史 terminal cancellation 不需搬移或刪除。

## Verification

以下為切換 Gemma 前的歷史測試紀錄。`test_material_discard.py` 使用 disposable PostgreSQL、synthetic PDFs、Event 排序的競態、stubbed worker 與 quarantine fault injection。`test_processing_cancellation.py` 驗證新取消的 intent gate 及 legacy honor/recovery；pipeline checkpoint tests 保持。Processing／material-discard Playwright 使用隔離 mocked API，涵蓋 desktop/mobile、409、503、reload、404、舊 map/session 與卡片數量更新。沒有 GPU、OCR model、Qwen inference 或 Assessment qualification，也沒有模型／程序 kill 控制。

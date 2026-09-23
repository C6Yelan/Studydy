# 教材庫與重新開啟

登入後進入首頁，從 Sidebar「我的教材」或首頁 CTA 開啟教材庫（`/materials`）。教材列表由後端依目前 learner 查詢；新瀏覽器輸入相同帳密後，
不需 localStorage、教材 UUID 或保存過的網址，就能找到自己的教材。

- 「上傳教材」前往 `/upload`，先建立教材草稿，再保存來源檔及檔名供辨識。
- 每份教材顯示名稱、上傳時間、大小和最新處理狀態；只有完成上傳、尚未開始處理的教材也會列出。
- 「開啟知識地圖」讀取最近已發布的 exact Knowledge Structure，Map 左側學習導覽保留建議順序。
- 教材名稱為純文字；卡片直接提供整理、重試、處理狀態或學習入口，並提供原檔連結。
- 前端以目前 head 為主要地圖，不提供歷史地圖版本選單。既有題目與作答可從「先前題目與作答」找回；來源管理及增量追加見 [B3-A](source-revisions.md)。
- 最近處理若失敗或取消，先前成功或 partial 的已發布版本仍保留，兩種狀態分開顯示。
- 處理中與正在取消並刪除時自動更新狀態；錯誤有重新讀取與返回教材庫的出口。正常 collection 不提供手動重新整理。

重新開啟只讀取既有 Material、Artifact、ProcessingRun 和 KnowledgeStructure，不呼叫模型、
不新增紀錄。所有教材皆可由 owner 確認刪除；PDF、處理紀錄、知識地圖與該教材的學習／作答資料一併安全清除。有既有學習時，只以最新可用 structure 的 exact run/revision 對應 state 提供「繼續學習」或「查看學習成果」。見[學習恢復](learning-resume.md)。

## API 與 migration

| 入口 | 行為 |
|---|---|
| `GET /v1/materials` | `material-library/v1`，列出目前 learner 的全部教材 |
| `GET /v1/materials/{material_id}` | `material-library-item/v1`，只允許 owner 讀取教材 binding |
| `DELETE /v1/materials/{material_id}` | HTTP 202、`material-discard/v1`；owner-scoped 刪除該教材與全部衍生學習資料 |
| `POST /v1/materials` | 以必填的 `display_name` 建立教材草稿；檔案另由來源上傳入口加入 |

列表與單份教材回應包含 `latest_attempt` 和 `available_structures`。`latest_attempt.cancel_requested_at` 與 status 表示「正在取消並刪除教材」或歷史 terminal「已取消處理」；刪除 authority 與保護條件見[取消並刪除契約](material-processing-cancellation.md)。後者每筆包含 exact `run_id`、
`knowledge_structure_revision`、發布時間及 succeeded／partial 狀態，不以最新失敗作業
代替已發布結果。Map、處理作業與 PDF 仍使用既有 GET 入口及 server owner 檢查。
新入口沿用 session cookie 和 `private, no-store`，不接受 client 指定 learner。

教材名稱為必填，最多 200 個 Unicode 字元，不接受空白名稱、控制字元或路徑分隔符號。
相同 idempotency key 搭配不同建立名稱會回傳 409；之後可由 owner 明確重新命名。
`0001_identity_and_materials.sql` 在 `materials` 對名稱設必填與長度約束。
保持原 DB/PDF store，在既有私有環境設定下執行：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -c 'from runtime.storage.migrations import run_migrations; print(run_migrations())'
```

空 DB 回傳 `(1, 2, 3, 4)`，相同 baseline 重跑回傳 `()`；舊帳本需先完成 [baseline 接軌](accounts.md#migration)。

## 本地驗證

先 `npm --prefix frontend run build`，再依 [testing.md](testing.md) 執行 runtime tests。
教材庫 Browser fixture 使用本地 API、PostgreSQL 和 production frontend；登入後只從教材名稱
導航，重新建立 browser context 再次登入，分別開啟先前成功及 partial 版本。

測試比對七張產品表的完整內容摘要與筆數（含 Material、Artifact、Run、KS、StudySession、
Assessment、AnswerEvent），確認純 reopen 不變更資料；攔截後端 HTTP transport 確認零模型外呼。
來源為既有 controlled fixtures，不啟動模型、OCR 或雲端 Pod，不宣稱完成正式模型或整合驗收。

教材卡片的管理選單提供重新命名與刪除。`POST /v1/materials/{material_id}/rename` 使用 `material-rename/v1`，回傳既有 `material-library-item/v1`；僅修改 display_name，trim 後 1–200 Unicode 字元，不接受控制字元。Rename 與 delete 都鎖定 owner 的 exact Material row；已保存 delete intent 時拒絕 rename。沒有新增 migration。

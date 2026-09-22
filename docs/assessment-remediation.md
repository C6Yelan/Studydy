# 錯題補強與本輪判定（B05-R）

初篩交卷後直接顯示答錯教材重點與來源。使用者閱讀後點「開始補強 N 題」，系統針對目前所有待補強重點準備新題。閱讀不另存 event；離開再回來仍由既有作答還原結果。

## 流程與投影

- 初篩答對：`diagnostic_pass`。
- 初篩錯答且最新補強尚未答對：`needs_review`。
- 新補強題答對：`remediation_pass`；再次答錯仍是 `needs_review`，下次補強只包含仍錯的重點。
- 未作答：`unanswered`；未出題：`unavailable`。兩者不算錯、不進補強，也不冒充通過。
- 每組新補強需使用者明確點擊，不自動連續出題。若已有同觀念 active set，接續原題組。

Cycle outcome 依資料推導：有 active set 為 `in_progress`；有錯誤重點為 `needs_review`；所有目標通過且沒有未檢測為 `passed`；其餘為 `incomplete`。

`can_create_remediation` 要求初篩 root completed、StudySession 可操作、同觀念沒有 active set，且 pending_count > 0。目標為所有當前 `needs_review`，policy 是 `needs-review-points/v1`。

Learner next action：有 active set → `continue_set`；needs_review → `remediate`；passed / incomplete 且無 active set → `advance` 或 `complete`。Concept-level no-safe 暫緩功能保持獨立。

## 題目與掌握邊界

初篩與補強沿用相同 worker、題目生成與驗證流程。題組封存後不替換題目；整組交卷以同一交易保存答案與完成狀態。來源、正誤與舊 AnswerEvent 不改寫。

補強答案在投影中保持 `assisted`：計入作答與本次改善，不補足或恢復獨立掌握證據。既有 mastery qualification、跨來源繼承及不可變 origin 保留。

## Storage 與 API

`assessment_sets.kind`、`diagnostic_set_id`、`remediation_origin_scope` 與 origin immutability trigger 維持補強歸屬。Migration 0014 移除不再需要的流程確認 metadata，並更新既有補強 target_plan 的 policy 名稱；既有目標、題目、答案與來源保持不變。通用 `action_receipts` 保留重試／部分發布／提交的冪等能力。

| 操作 | API |
|---|---|
| 首次初篩 | `POST /v1/study-sessions/{sid}/assessment-sets` |
| 補強 | `POST .../assessment-sets/{root}/remediation` |
| 整組交卷 | `POST .../assessment-sets/{set}/submissions` |
| 重試未備妥項目 | `POST .../assessment-sets/{set}/retry` |
| 部分發布 | `POST .../assessment-sets/{set}/publish-partial` |

建立補強使用初篩版本與 Idempotency-Key。同意圖重播回同一題組；版本衝突或同觀念 active set 不建立另一組。GET、reload 與重新登入不出題。

公開契約：`assessment-set/v3`、`learner-progress/v4`、`study-resume/v5`。所有受控 consumer 一起使用目前版本。題組清單 `assessment-set-list/v3` 的 shape 未變。

Internal cancellation 僅用於終止的 StudySession 等安全處理，不提供學生取消 action。

## 驗證

隔離 DB 測試涵蓋：四題初篩二對二錯、直接選取所有錯誤重點、部分補強成功後只重問剩餘錯題、全部通過、未作答／未檢測、冪等、跨觀念與 resume。Migration 升級核對完整題目、答案、題組項目與補強歸屬。

真 API browser 在桌面與手機走初篩 → 直接補強 → 再錯 → 再次補強 → 通過，並測回應遺失與重新登入。模型由受控 fixture 提供，這是功能驗證，不是題目品質驗收。

完成結果的主要 CTA 依最新 next_action 顯示下一個觀念或完成本次學習。StudySessionPage 使用 `guidance-apply/v2` 套用 guidance_revision，成功後清除上一題組 route 並 resume 同一 session。失敗留在結果，stale 只重新讀取下一步；不以地圖或前端路徑索引作中轉。

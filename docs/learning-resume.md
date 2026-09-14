# 原學習、題目與作答恢復

每個 learner/material/structure revision 對應一份持續保存的學習 state（內部仍稱 StudySession）。
教材庫只開啟最新可用 structure 對應的 state；不呈現 Session／structure 歷史選單。
舊 rows、題目與答案保留，既有 exact deep link 仍可恢復，不跨 revision 轉移進度。

- 保存原 current concept、session status、no-safe claim、deferred concept 與 progress。
- 進入 session 時，預設讀取目前概念最近產生的題目；已完成 session 則讀最近的題目。
- 「題目與作答紀錄」可選取舊題。所選 Assessment 寫入瀏覽器網址，reload 仍讀同一題。
- 未答題恢復原問題及選項；已答題恢復原 AnswerEvent、選項及回饋。過去位置或已完成
  session 的未答題只供查看，不能透過舊題提交改變目前狀態。
- 「返回最新進度」回到目前概念預設題目；不套用 guidance 或新增題目。
- 提交後若斷線／回應遺失，按「查回作答結果」進行唯讀查詢。若後端已提交，就顯示原結果；
  未提交則仍為未作答。既有相同 idempotency key 重送維持同一結果，衝突仍拒絕。
- Map 的「開始學習」只 ensure state 存在；已有 state 時切換概念使用 focus setter，答案與掌握度保留。
- Completed state 只提供「查看學習成果」，不重新建立或清空進度。
- Assessment「題目與作答紀錄」保留，跨 legacy sessions 的事件不合併。

## API／資料契約

唯讀入口：

```text
GET /v1/materials/{material_id}/knowledge-structures/{structure_revision}/study-sessions/{study_session_id}/resume
    ?run_id={run_id}&assessment_revision={optional_assessment_revision}
```

`study-resume/v1` 回應包含 `session`、`run_id`、`source_artifact_id`、`knowledge_structure`、
`progress`、`assessments` 和 `selected_assessment_revision`。每筆題目紀錄只有 public Assessment、
產生時間、`can_submit` 與 nullable feedback；未答題不公開私人答案。

後端檢查目前 learner、material、run、exact KS revision、session 與選取題目是否一致。
錯 owner／binding／不屬於該 session 的題目回 404；無 session 回 401；讀取期間學習狀態
改變回 409，讓前端重讀。新入口沿用 private/no-store，拒絕 client 指定 learner。

`material-library-item/v2` 的 `study_sessions` 提供原 session/revision/run 的唯讀連結；
既有 `study-session/v2` 保留已保存的 `no_safe_claim_ids`。其餘 scoring、mastery、stale、
idempotency 與 guidance authority 不變。沒有 migration、新資料表、新 dependency 或模型改動。

[本地測試](testing.md#learning-resume-regression-local-only) 使用真 Browser/API/PostgreSQL 與
controlled fixtures，驗證 reload、新 profile 重登、真提交後 response 遺失，以及 restore 零寫入／
零模型呼叫；不取代後續單元 D 的整合與正常啟停驗收。

## Persistent state ensure / focus

`POST /v1/study-sessions`（`study-session-create/v2`）鎖定 exact KnowledgeStructure row 後，
優先選最新 active/no_safe，否則最新 completed；以 started_at DESC、study_session_id DESC 決定同序。
只有首次建立保存 initial key/fingerprint；該 key 的不相容 request 仍 conflict。
其他 key 的 ensure 是唯讀，不切換 concept。Legacy duplicate 不合併或刪除。

`POST /v1/study-sessions/{id}/focus` 接受 `study-session-focus/v1` 與 `current_concept_id`，
回傳既有 `study-session/v2`。它使用原 setter，驗證 ownership/bound concept、保留答案與 event watermark，
completed 不可重新開啟。不使用 Idempotency-Key；重複設定同概念結果相同。
教材庫 `study_sessions` 每個 revision 最多一筆 canonical link。無 migration 或新資料表。

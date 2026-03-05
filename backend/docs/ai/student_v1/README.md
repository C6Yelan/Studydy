# Student Schema v1

這個資料夾定義 Student Model 事件資料的最小契約。
目前提供 `student_event.schema.v1.json`，先聚焦事件記錄一致性。

欄位重點：
- `event_type`：事件類型（例如答題提交、學習停留）。
- `concept_id` 或 `ref_id`：事件關聯的概念或外部參照，至少要有一個。
- `is_correct`：事件是否正確（布林值）。
- `payload.time_spent_ms`：耗時毫秒數，固定放在 payload 內。

此設計讓資料可先穩定落地，後續再依分析需求新增更多 payload 欄位。
核心要求是欄位固定且可驗證，避免事件資料格式漂移。

# Tutor Schema v1

這個資料夾提供 Tutor Agent 的最小輸出契約。
目前分成策略層與訊息層兩份 schema。

`tutor_action_plan.schema.v1.json` 重點：
- `action_type`：這一步要做的教學動作。
- `target_concepts`：目標概念陣列。
- `difficulty_target`：預期難度區間。
- `hint_level`：提示強度（0~5）。

`tutor_message.schema.v1.json` 重點：
- `summary`：本輪重點摘要。
- `feedback`：對學生當下表現的回饋。
- `next_steps`：後續建議步驟。
- `evidence_refs`：引用教材來源（含 `chunk_id` + `locator`）。

這些欄位先確保 Tutor 輸出可驗證、可追溯，後續再擴充語氣或策略細節。

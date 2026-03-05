# Question Schema v1

這個資料夾提供 Question Agent 的最小輸出契約。
目前先定義 `question_item.schema.v1.json`，讓題目資料可以穩定驗證。

欄位重點：
- `question_id`：題目唯一識別碼。
- `concept_id`：題目對應的概念代碼。
- `difficulty`：題目難度標記。
- `stem`：題幹內容。
- `options`：選項陣列（只有選擇題才需要）。
- `answer_key`：正解（字串、索引或答案集合）。
- `rationale`：解題說明。
- `evidence_refs`：引用來源清單，至少要有 `chunk_id` 與 `locator`。

`locator` 用來回到原始教材位置（頁碼、段落、投影片或字元範圍）。
這份 schema 目標是先支援 Question Agent 的最小可用流程。

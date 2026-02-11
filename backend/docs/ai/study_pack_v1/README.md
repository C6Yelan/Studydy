# Study Pack Schema v1 (T0)

此資料夾定義 Studydy AI 的最小可交付輸出（Study Pack）資料契約：
- JSON Schema（v1）
- 欄位字典（v1）
- 驗收規則（v1）
- Golden samples（用於測試與回歸）

v1 採用「嚴格輸出策略」：在保留 Draft 2020-12 的前提下，加入文字長度與陣列數量上限，並增加 Root 非空 Gate（`key_points` / `glossary` / `quiz` / `story_nodes` 至少一個區塊非空），以降低空包與格式漂移。

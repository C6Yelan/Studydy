# Study Pack JSON Schema v1 (T0)

此資料夾定義 Studydy AI 在 T0 的輸出契約與驗收資料：
- JSON Schema（v1）
- 欄位字典（v1）
- 驗收規則（v1）
- Golden samples（固定回歸樣本）

## T0 任務定義與輸出規格鎖定
- 使用 JSON Schema Draft 2020-12。
- 採用嚴格輸出策略：加入文字長度與陣列數量上限，降低格式漂移。
- Root 非空 Gate 使用 `anyOf`：`key_points` / `glossary` / `quiz` / `story_nodes` 至少一個 `minItems: 1`（不含 `outline`）。
- Quiz 固定使用 `answer_index`（非 `answer`），`options` 固定 4 個且 `uniqueItems: true`。
- 一致性規則：若有 `explanation`，必須同時存在 `answer_index`（`dependentRequired`）。
- `default` 只屬於 annotation，不會在 validation 時自動補值。

## Schema Gate
- 使用 `python-jsonschema` 的 `Draft202012Validator` 驗證 schema 與 instance。
- 使用 `pytest` 驗證固定 golden samples（`minimal_valid.json` / `typical.json` / `edge_case.json`）。
- 測試入口：`backend/tests/test_study_pack_schema_v1.py`。

## T5 Dataset Schema Gate（Training Data）
- 指令：
  - `python backend/scripts/datasets/validate_study_pack_dataset.py --input <jsonl>`
- 預設會使用 schema：`backend/docs/ai/study_pack_v1/study_pack.schema.v1.json`。
- output 預設讀取每筆 record 的 `output` 欄位；可用 `--output-key` 指定其他欄位。
- 產出檔案（預設與 input 同資料夾）：
  - report：`<input_filename>.report.json`
  - quarantine：`<input_filename>.quarantine.jsonl`
- report 用途：提供總覽（`total/passed/failed/pass_rate`）與每個錯誤的 `line_number/record_id/error_path/message`。
- quarantine 用途：收錄所有 fail 的原始 record，並附加 `validation_errors` 供後續人工排查或修復流程使用。

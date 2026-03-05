# Instruction Dataset v1 (T4)

此文件定義 Studydy T4 產出的可訓練指令資料集（SFT）格式，以及對應的 build / validate 使用方式。

## 輸入來源（來自 T3）
- 優先輸入：`backend/datasets_local/exports/chunks/*.chunks.v1.jsonl`
- 每筆 chunk 至少含：`chunk_id`, `text`, `meta`
- `meta` 需可追溯來源定位（例如 `source_relative_path`, `dataset_id`, `page_*` / `paragraph_*` / `slide_*`, `title`, `heading`）

## SFT JSONL 欄位（每行一筆 JSON）
- `id`: instruction sample id（例：`ins-ch-...`）
- `dataset_version`: 固定 `instruction_dataset.v1`
- `split`: `train` / `valid` / `test`（由 `--split` + `--seed` 決定）
- `task`: 任務類型（目前 MVP 先產 `domain`，保留 `question`/`tutor` 擴充位）
- `chunk_id`: 對應 T3 chunk id
- `source`: 追溯資訊
- `meta`: 原始 chunk meta（保留定位與來源欄位）
- `prompt` + `completion`（`prompt_completion` 格式）或 `messages`（`conversational` 格式）
- `output_json`: 目標 Study Pack JSON object（需符合 `study_pack.schema.v1.json`）

`source` 物件建議欄位：
- `doc_id`
- `dataset_id`
- `chunk_id`
- `source_relative_path`
- `sha256`
- `locator`（包含 page/paragraph/slide/title/heading 等）

## 可選 DPO JSONL 欄位
- `id`
- `dataset_version`: 固定 `preference_dataset.v1`
- `split`
- `prompt`
- `chosen`
- `rejected`
- `source`
- `chunk_id`

## Build 指令
在 repo root 執行：

```bash
python backend/scripts/datasets/build_instruction_dataset.py \
  --input backend/datasets_local/exports/chunks \
  --input-format jsonl \
  --out-dir backend/datasets_local/exports \
  --study-pack-schema backend/docs/ai/study_pack_v1/study_pack.schema.v1.json \
  --task domain \
  --split train:0.9,valid:0.1 \
  --seed 42 \
  --max-context-chars 8000 \
  --format prompt_completion
```

輸出檔案（依 split）：
- `backend/datasets_local/exports/instruction_dataset.v1.train.jsonl`
- `backend/datasets_local/exports/instruction_dataset.v1.valid.jsonl`
- `backend/datasets_local/exports/instruction_dataset.build_report.v1.json`

啟用 DPO 輸出：

```bash
python backend/scripts/datasets/build_instruction_dataset.py \
  --input backend/datasets_local/exports/chunks \
  --input-format jsonl \
  --with-dpo
```

## Validate 指令
在 repo root 執行：

```bash
python backend/scripts/datasets/validate_instruction_dataset.py \
  --input backend/datasets_local/exports \
  --study-pack-schema backend/docs/ai/study_pack_v1/study_pack.schema.v1.json
```

預設會輸出：
- `backend/datasets_local/exports/instruction_dataset.validation_report.v1.json`
- `backend/datasets_local/exports/instruction_dataset.quarantine.v1.jsonl`（invalid ids）

## Gate 重點
- 每筆資料必須可追溯到 `chunk_id` 與原始定位（`source/meta/locator`）。
- 若含 `output_json`，必須可通過 Study Pack schema v1 驗證。
- `datasets_local` 屬本機產物，禁止提交資料內容到 Git（僅保留 `.gitkeep`）。

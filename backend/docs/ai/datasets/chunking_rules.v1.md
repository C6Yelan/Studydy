# Chunking Rules v1 (Chunk -> Intermediate)

## Input / Output
- Manifest input: `backend/docs/ai/datasets/manifest.v1.yaml`
- Chunk output dir: `backend/datasets_local/exports/chunks`
- Per-dataset JSONL: `{dataset_id}.chunks.v1.jsonl`
- Stats output: `chunk_stats.v1.json`

> `datasets_local` 產物為本機資料，不應提交到版本庫（`.gitignore` 只允許 `.gitkeep`）。

## Source Selection (raw/redacted)
- 逐一讀取 manifest 的 `datasets[*].files[*]`。
- 以 manifest 的 `files[].relative_path` 當 traceability source key。
- 實際讀檔時優先 redacted：若 `datasets_local/redacted/` 存在同路徑或同檔名檔案則使用它，否則使用 raw。

## Supported Formats & Unit Boundaries
- `pdf` (pypdf): 以 page 為 unit（1-based）
- `docx` (python-docx): 以 paragraph 為 unit（0-based）
- `pptx` (python-pptx): 以 slide 為 unit（1-based）
- `txt` / `md`: 以空行切段為 paragraph unit（0-based）

## Chunking Parameters
- `max_chars`：預設 `1200`（可 CLI 覆寫）
- `min_chars`：預設 `200`（可 CLI 覆寫）
- `overlap_chars`：預設 `0`（可 CLI 覆寫）

規則：
1. 優先保持 unit 邊界（paragraph/page/slide）合併，避免破壞追溯性。
2. 若單一 unit 超過 `max_chars`，允許 unit 內切分：
   - 先找換行
   - 再找句尾標點（`. ? !` 與 `。？！`）
   - 最後才硬切
3. 盡量避免過小 chunk；若可在不超過 `max_chars` 前提下，會合併小於 `min_chars` 的鄰近 chunk。

## Chunk Record Schema (JSONL each line)
每筆至少包含：
- `chunk_id`: `ch-{dataset_id}-{seq:06d}`
- `text`: chunk text
- `meta` (object):
  - required:
    - `source_relative_path` (manifest 的 `files[].relative_path`)
    - `file_type`
    - `sha256`
    - `dataset_id`
    - `created_at` (UTC ISO8601)
    - `char_count`
  - traceability (依格式):
    - PDF: `page_start`, `page_end`
    - DOCX/TXT/MD: `paragraph_start`, `paragraph_end`
    - PPTX: `slide_start`, `slide_end`
  - optional:
    - `title`
    - `heading`

完整 schema 參考：
- `backend/docs/ai/datasets/document_chunks.schema.v1.json`

## Stats (`chunk_stats.v1.json`)
至少包含：
- `datasets.{dataset_id}.chunk_count`
- `datasets.{dataset_id}.total_chars`
- `datasets.{dataset_id}.avg_chars`
- `datasets.{dataset_id}.min_chars`
- `datasets.{dataset_id}.max_chars`

並包含整體 totals（dataset_count / chunk_count / total_chars）。

## CLI
Script:
- `backend/scripts/datasets/build_chunks.py`

常用指令（在 repo root）：

```bash
python backend/scripts/datasets/build_chunks.py
```

指定 manifest / output：

```bash
python backend/scripts/datasets/build_chunks.py \
  --manifest backend/docs/ai/datasets/manifest.v1.yaml \
  --out-dir backend/datasets_local/exports/chunks
```

覆寫 chunk 參數：

```bash
python backend/scripts/datasets/build_chunks.py \
  --max-chars 1000 \
  --min-chars 180 \
  --overlap-chars 0
```

僅跑單一 dataset：

```bash
python backend/scripts/datasets/build_chunks.py \
  --dataset-id ds-example
```

Dry run（只印統計，不寫檔）：

```bash
python backend/scripts/datasets/build_chunks.py --dry-run
```

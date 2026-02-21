# AI Pipeline T2 — Document Extract（Extract -> Raw Text + Metadata）

此文件說明 Studydy 後端 T2 抽取流程，目標是把 `PDF/DOCX/PPTX` 轉成可供後續 T3 chunking 直接使用的穩定輸出。

## 支援格式
- `PDF`：以 page 為分段單位
- `DOCX`：以 paragraph 為分段單位（MVP 不抽 table cells）
- `PPTX`：以 slide 為分段單位

## 抽取腳本
- 檔案：`backend/scripts/datasets/extract_documents.py`

### 常用指令
在 repo 根目錄執行：

```bash
python backend/scripts/datasets/extract_documents.py \
  --input backend/datasets_local/redacted \
  --output backend/datasets_local/raw
```

測試也可在 repo root 執行：`PYTHONPATH=backend python -m pytest -q backend/tests`

指定單一檔案：

```bash
python backend/scripts/datasets/extract_documents.py \
  --input /path/to/file.docx \
  --output backend/datasets_local/raw
```

指定 manifest（依 `datasets[*].files[*].relative_path` 抽取）：

```bash
python backend/scripts/datasets/extract_documents.py \
  --input backend/datasets_local/redacted \
  --manifest backend/docs/ai/datasets/manifest.v1.yaml \
  --output backend/datasets_local/raw
```

指定 run id（避免覆蓋、利於追蹤）：

```bash
python backend/scripts/datasets/extract_documents.py \
  --input backend/datasets_local/redacted \
  --output backend/datasets_local/raw \
  --run-id run-20260219T120000Z-demo
```

失敗即停止：

```bash
python backend/scripts/datasets/extract_documents.py \
  --input backend/datasets_local/redacted \
  --output backend/datasets_local/raw \
  --fail-fast
```

## CLI 參數
- `--input <path>`：資料夾或單一檔案（預設 `backend/datasets_local/redacted`）
- `--manifest <path>`：可選；提供時以 manifest 內檔案清單為準
- `--output <path>`：輸出根目錄（預設 `backend/datasets_local/raw`）
- `--run-id <id>`：可選；未提供會自動生成
- `--fail-fast`：可選；遇到第一個失敗文件即停止

## 輸出結構

```text
<output>/
  runs/<run_id>/
    documents/<doc_uid>/
      raw.jsonl
      meta.json
      extract.log
  reports/<run_id>.json
```

## raw.jsonl（segment 一行一筆）
每筆至少包含：
- `segment_id`（uuid）
- `doc_uid`（目前為檔案 `sha256`）
- `source_path`
- `file_type`：`pdf|docx|pptx`
- `unit_type`：`page|slide|paragraph`
- `unit_index`（從 1 開始）
- `text`（可為空字串）
- `locator`（對應 unit；例如 `{ "page": 3 }`）
- `extracted_at`（ISO8601 UTC）
- `extractor_version`（固定 `t2-v1`）

## meta.json（document-level）
至少包含：
- `doc_uid`, `filename`, `file_type`, `size_bytes`, `sha256`, `extracted_at`, `run_id`
- `stats`：`segments`, `non_empty_segments`, `empty_segments`
- `warnings`：例如掃描型 PDF 無法抽文字、DOCX table 未抽取
- `errors`：抽取/寫檔失敗資訊

## report.json（batch-level）
至少包含：
- `run_id`, `started_at`, `finished_at`, `duration_seconds`
- `totals`：`discovered_documents`, `processed_documents`, `succeeded`, `failed`, `warnings`
- `documents`：每個文件的成功/失敗、警告、錯誤、輸出位置

## Traceability 保證
- 每個 segment 都帶有 `doc_uid + source_path + unit_index + locator`
- PDF 可回到 page、PPTX 可回到 slide、DOCX 可回到 paragraph

## Raw 永不覆蓋策略
- 若 `run_id` 已存在，腳本會直接報錯並停止
- 重跑請使用新 `run_id`（或不提供 `--run-id` 由系統自動生成）

## 常見失敗與排查
- 不支援格式：確認副檔名為 `.pdf/.docx/.pptx`
- 掃描 PDF 抽不到字：`raw.jsonl` 會有空文字 segment，並在 `warnings` 標記
- manifest 路徑錯誤：確認 `relative_path` 指向存在檔案
- run_id 衝突：改用新 `--run-id`
- 套件缺失：重新安裝 `backend/requirements.txt`

## 安全與提交注意事項
- `datasets_local` 只供本機資料管線使用，不提交實際抽取產物
- 禁止提交任何 secrets（`.env`、金鑰、連線字串等）

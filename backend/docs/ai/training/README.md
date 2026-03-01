# Training Config v1（T6）

此資料夾定義 T6 的訓練設定契約與 run 初始化流程，目標是讓後續訓練（T7）具備可重現、可追溯的 metadata 基礎。

## 檔案
- `training_config.schema.v1.json`：訓練設定 JSON Schema（Draft 2020-12）
- `training_config.example.v1.yaml`：示範設定（小模型 + 保守超參數，不會自動觸發訓練）

## 欄位說明（Schema v1）
- `version`：固定 `v1`
- `base_model`：基礎模型名稱（例如 `Qwen/Qwen3-0.6B`）
- `training_method`：`lora` 或 `qlora`
- `dataset.train_path`：訓練資料 JSONL 路徑（必要）
- `dataset.valid_path`：驗證資料 JSONL 路徑（可選）
- `dataset.dataset_version`：資料版本字串（可選；若未提供，仍會在 run init 時計算檔案 hash）
- `hyperparams.learning_rate`
- `hyperparams.epochs`
- `hyperparams.per_device_train_batch_size`
- `hyperparams.gradient_accumulation_steps`
- `hyperparams.seed`
- `hyperparams.max_seq_len`
- `lora.r`
- `lora.lora_alpha`
- `lora.lora_dropout`
- `lora.target_modules`：可選 target modules 清單
- `output`：可選；若提供，僅允許 `runs_dir`
- `output.runs_dir`：run 輸出根目錄（預設 `backend/datasets_local/training/runs`）

### default 行為（重要）
- JSON Schema 的 `default` 只是一種 annotation，不會在 validation 階段自動補值。
- `validate_training_config.py` 只做驗證，不會修改 config。
- `init_training_run.py` 會讀取 schema 內 `output.runs_dir.default` 作為預設值；若 schema 讀取失敗或缺值，才 fallback 到 `backend/datasets_local/training/runs`。
- 因此 config 可省略 `output`，仍可正常 init run。

## 驗證設定檔
在 repo root 執行：

```bash
python backend/scripts/training/validate_training_config.py \
  --config backend/docs/ai/training/training_config.example.v1.yaml
```

## 初始化 run metadata（不會訓練）
在 repo root 執行：

```bash
python backend/scripts/training/init_training_run.py \
  --config backend/docs/ai/training/training_config.example.v1.yaml
```

初始化後會在 `<runs_dir>/<run_id>/` 產出：
- `run_meta.json`
- `config.snapshot.yaml`
- `run_summary.md`

`run_meta.json` 至少包含：
- `run_id`
- `created_at_utc`
- `git_commit`（若無法取得則為 `null`）
- `config_path` + `config_snapshot`
- `dataset_fingerprint`（`train/valid` 檔案內容 `sha256` + `dataset_version`）
- `base_model`、`training_method`、`hyperparams`、`lora`
- `output.runs_dir`（最終使用值：使用者設定或 schema default）

# AI Flow T1: 資料來源與授權盤點

## 1. 資料分級規則（allowed_use）

| 等級 | 判定條件 | 允許用途 | 必要動作 |
| --- | --- | --- | --- |
| `train` | 授權明確、可追溯；已完成去識別化審核；風險可控 | 可用於訓練與推論 | `license.evidence` 必填；`privacy.redaction_status=done` |
| `infer_only` | 授權不明或仍在審核；或去識別化尚未完成 | 僅可在受控流程推論，不可訓練 | 補齊授權證據與去識別化後才可升級 |
| `blocked` | 明確禁止再利用、含高度敏感資料、法務/合規拒絕 | 不可訓練、不可推論 | 隔離保存或移除，保留決策紀錄 |

### 判定表（快速決策）

| 授權狀態 | 個資狀態 | 建議等級 |
| --- | --- | --- |
| 明確可再利用（例：CC BY 4.0）且有證據 | 已完成去識別化 | `train` |
| 授權不明（`unknown`/`TBD`） | 任意 | `infer_only` |
| 授權禁止或使用條款衝突 | 任意 | `blocked` |
| 授權明確 | 未完成去識別化 | `infer_only` |

## 2. 授權與風險處理原則

1. 來源優先使用可再利用授權（例如 CC 系列、機構明確授權）。
2. `license.type` 與 `license.evidence` 必須可追溯到原始證據（URL、合約編號、書面許可）。
3. 授權不明一律先標記 `infer_only`，不得標記為 `train`。
4. 若授權條款限制改作、商用、再散布，需由人工審核後再決定是否 `blocked`。
5. 所有判定結果要寫回 `manifest.v1.yaml`，避免口頭決策。

## 3. 去識別化規則

### 必須移除的個資欄位

1. 姓名、學號/工號、班級座號
2. Email、電話、地址
3. 身分證/護照/居留證字號
4. 生日、醫療/成績等敏感識別資訊
5. 可反查個人的外部 ID（社群帳號、雲端連結中的 user id）

### Regex + 人工複核流程

1. 先以規則掃描（例）：
   - Email：`[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}`
   - 手機（台灣常見）：`(\+886-?|0)?9\d{8}`
   - 身分證字號（台灣常見）：`[A-Z][12]\d{8}`
2. 將命中內容做遮罩或替換（例如 `[REDACTED_EMAIL]`）。
3. 人工二次複核抽樣與高風險段落，確認無漏網資訊。
4. 複核完成後，更新 manifest 的 `privacy.redaction_status`、`reviewer`、`reviewed_at`。

### 輸出路徑規範

- 原始檔：`backend/datasets_local/raw/`
- 去識別化中介或結果：`backend/datasets_local/redacted/`
- 匯出產物（可再處理資料）：`backend/datasets_local/exports/`

## 4. T1 Gate 驗收清單

1. `manifest.v1.yaml` 必備欄位：
   - `version`
   - `datasets[]`
   - `dataset_id`
   - `allowed_use`
   - `license.type`
   - `license.evidence`
   - `privacy.redaction_status`
   - `files[].relative_path`
   - `files[].sha256`
   - `files[].size_bytes`
   - `updated_at`
2. `allowed_use=train` 時：
   - `license.type` 不可為 `unknown` 或 `TBD`
   - `license.evidence` 不可空白
3. 任何資料若授權不明，預設不得進訓練集（僅 `infer_only` 或 `blocked`）。
4. 僅提交 metadata/模板/腳本/文件；不得提交任何真實教材內容。

## 5. dataset_id 生成規則

1. 由檔案相對路徑生成：`ds-{stem}-{ext}-{hash8}`。
2. `stem` 與 `ext` 經過 slugify（不可用字元會被正規化；空副檔名用 `bin`）。
3. `hash8` 取 `sha1(relative_path_as_posix)` 前 8 碼，確保中文/非英數檔名也能穩定去碰撞。
4. build 時若 manifest 內已存在同 `files[].relative_path`，會沿用原本 `dataset_id`，避免演算法更新後重複新增。

## 6. 本地執行方式

1. 將本機來源檔放到 `backend/datasets_local/raw/`（此路徑不進版控）。
2. 產生/更新 manifest：
   - `python scripts/datasets/build_manifest.py`
3. 驗證 manifest：
   - `python scripts/datasets/validate_manifest.py`
4. 跑自動化測試：
   - `pytest -q tests/test_dataset_manifest.py`

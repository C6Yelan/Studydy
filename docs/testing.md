# 測試

## Material review browser regression

此 regression 不攔截產品 API，會用 harness 產生的安全 PDF 經真 local backend 建立 material 與 persisted `material_processing_run`，並驗證 API、資料庫與 frontend wiring。OCR、Concept 與 Knowledge Map producer 使用 deterministic test implementation，因此不宣稱驗證真實 OCR、Qwen、Relation Evidence Gate 或 mDeBERTa；真 local AI 必須另外執行下方測試與 production smoke。

先從 `frontend/` 安裝前端套件與 Playwright Chromium：

```bash
npm ci
npm run e2e:install
```

再依作業系統慣例啟用 `backend` 的虛擬環境，確認 `python` 指向該環境，並從 repository root 執行：

```bash
python backend/tests/runtime/material_review_e2e_runner.py --list
python backend/tests/runtime/material_review_e2e_runner.py
```

Python runner 會確認固定 port `4173`、`8001` 未被占用，建立 disposable PostgreSQL，套用 migration，再啟動自己擁有的 Uvicorn、Vite 與 Playwright child。成功、失敗、timeout 或 signal 都只清理這次建立的 process group 與 PostgreSQL container。若直接執行 inner `playwright test`，因缺少 harness identity 會以 `E2E_HARNESS_REQUIRED` 結束。

失敗時 trace 與 screenshot 位於 ignored `test-results/`，HTML report 位於 ignored `playwright-report/`；不得提交這些 runtime artifacts。Chromium 由標準 Playwright cache 管理，可用 `npm run e2e:install` 安裝。

## Text-first local AI tests

Page render、Unlimited-OCR pipe、Qwen Concept validation 與 atomic output 的 deterministic tests 不載入模型，可從 repository root 執行：

```bash
PYTHONPATH=backend/src:local_ai/src backend/.venv/bin/pytest -q \
  local_ai/tests \
  backend/tests/test_local_ai_process.py \
  backend/tests/test_ocr_page_evidence.py \
  backend/tests/test_concept_generation.py \
  backend/tests/test_concept_evidence_output.py \
  backend/tests/test_text_first_run.py
```

公開模型 smoke 必須使用 `local_ai/runtime-lock.json` 綁定的離線 wheel、模型 revision、prompt 與 generation 設定。驗收時確認同一 product path 產生至少一個 page、Evidence block 與 Concept，狀態維持 `partial / needs_review / review`；再以相同輸入重跑，OCR 與 Qwen 呼叫數都必須為零。測試輸出只報告狀態、數量、延遲與固定 reason code，不保存 page image、完整 OCR／model text 或 raw pipe 內容。

## Fixed local runtime checks

本機 runtime 的預設 root 是 `~/.local/share/studydy`。OCR runtime 位於
`<root>/ocr/runtime`；若需使用其他整個 runtime root，只設定
`STUDYDY_LOCAL_RUNTIME_ROOT`（必須是絕對路徑）。沒有其他 runtime path、model
或 endpoint 的 root override。

這些命令都從 repository root 執行：

```bash
PYTHONPATH=backend/src python -m runtime.local_runtime sync
PYTHONPATH=backend/src python -m runtime.local_runtime sync --rollback
PYTHONPATH=backend/src python -m runtime.local_runtime verify
```

`verify` 只讀取並驗證目前已安裝的 runtime，沒有副作用。`sync` 是明確、另行授權
的操作，只 reconcile 已安裝 Studydy Python package 中的三個檔案：
`__init__.py`、`protocol.py`、`ocr_process.py` 與 `relation_process.py`。若有變更，會先保留四個檔案的完整
backup；`sync --rollback` 會從該 backup 還原。sync 不會在啟動時自動執行，也不會
進行下載、安裝或網路操作；在真實主機上執行會改動檔案，必須另行取得批准。

測試中的 disposable fixtures 只驗證這些 layout、驗證與 backup/rollback 邏輯。
真實主機 E2E 與 unseen-PDF 評估是另外核准的操作，不屬於 fixture 測試。

## Relation verifier checks

Relation production path 只接受 `contains`、`prerequisite` 與 `related`。前兩類必須先有 directional pair Evidence，再由固定 mDeBERTa child 驗證；`related` 不呼叫 NLI。以下 focused tests 會以真 subprocess boundary 區分 dependency missing、CUDA unavailable、model load failure、timeout 與 invalid response，並確認失敗時不建立 structural edge、也不回退到 Qwen：

Candidate selector 的固定上限是每份教材 128 pairs；超過時只使用 deterministic ranking 的前 128 pairs，Knowledge Map 必須回報 `partial` 與 `RELATION_PAIR_CEILING_EXCEEDED`。本次不調整該上限。

```bash
PYTHONPATH=backend/src:local_ai/src backend/.venv/bin/pytest -q \
  backend/tests/test_local_ai_process.py \
  backend/tests/test_knowledge_map.py \
  local_ai/tests/test_protocol.py \
  local_ai/tests/test_relation_process.py
```

`runtime.local_runtime verify` 先驗證 pinned package metadata、模型檔與 code hash；真正啟動 Relation child 時，還會由該 child 使用自己的 Python import `torch`／`transformers` 並回報 bounded startup reason。CUDA 不可用不得誤報成 dependency missing。真模型 acceptance 與實際教材 E2E 只在 CUDA host 執行。

## Resource intake

先準備一份 `resource-source-metadata/v1` JSON，並確認固定本機 runtime 已通過
`verify`。分析命令會處理 PDF 全頁；若單頁內容超過模型 context，會依 tokenizer
結果在該頁內分批，不需要設定頁數、block 數或執行秒數上限：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m learning_resources.resource_intake \
  analyze <PDF> --metadata <METADATA_JSON>
```

命令只會在 ignored `.studydy-runtime/resource-intake/candidates/` 產生可閱讀的
`review.md` 與 machine-readable `candidate.json`，並輸出兩者路徑；不會修改正式
resource library。人工檢查頁碼、label、每一筆 Evidence、授權與引用後，才使用分析
輸出的 exact candidate ID 與 SHA 發布：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m learning_resources.resource_intake \
  publish <CANDIDATE_ID> \
  --candidate-sha256 <CANDIDATE_SHA256> \
  --confirm <CANDIDATE_ID> \
  --source-pdf <PDF>
```

Concept prompt 只定義於 `local_ai/runtime-lock.json`；runtime 會驗證 prompt SHA 後再
呼叫本機 Qwen。模型每批只收到短 Evidence ID 與該批文字，正式 identity、頁面定位、
bbox 與 runtime metadata 均留在後端完成綁定。

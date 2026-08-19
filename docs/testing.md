# 測試

## Material review browser regression

此 regression 的 core full-stack case 不攔截產品 API，會用 harness 產生的安全 PDF 經真 local backend 建立 material 與 persisted `material_processing_run`，產生 Study Material Output v3 與 review-only Knowledge Map v2，並驗證 Evidence 可回查至來源 PDF 的同頁 locator。其餘 `page.route` 只用於 session setup 與代表性的 failure／review display；這些案例不宣稱為 full-stack。

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

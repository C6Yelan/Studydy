# 測試

## Formal text-first runtime

下列 deterministic tests 不載入模型、不連外，也不保存 page render、OCR/model 原文或 raw pipe 內容：

```bash
PYTHONPATH=backend/src:local_ai/src backend/.venv/bin/pytest -q \
  local_ai/tests \
  backend/tests/test_local_ai_process.py \
  backend/tests/test_ocr_page_evidence.py \
  backend/tests/test_concept_generation.py \
  backend/tests/test_concept_evidence_output.py \
  backend/tests/test_text_first_run.py \
  backend/tests/test_study_material_output.py \
  backend/tests/test_knowledge_map.py
```

Runtime、migration、持久化與 HTTP contract tests 會自行建立唯一的 PostgreSQL container，資料目錄使用 tmpfs；每個測試再建立一個全新空 database，確認 public schema 零資料表後才套用 migration：

```bash
PYTHONPATH=backend/src:local_ai/src backend/.venv/bin/pytest -q backend/tests/runtime
```

測試不接受既有 DSN，也不會讀取或清空開發／production database。完成、失敗或中斷時，只清理由本次測試建立的 database 與 container。

## Frontend contract 與 browser regression

從 `frontend/` 執行：

```bash
npm test
npm run typecheck
npm run build
npm run e2e:install
npm run e2e
```

Playwright 啟動本次測試自己的 Vite server，並只 mock 公開 v2 API；後端持久化與 API 整合由上述 PostgreSQL tests 負責。Browser cases 驗證 PDF client gate、33 頁固定失敗、review-only Map 與同頁 PDF locator，且不宣稱載入真模型。

## Real-model qualification boundary

公開模型 qualification 必須使用 `local_ai/runtime-lock.json` 的離線 wheel、模型 revision、prompt 與 generation 設定。一般 regression 不重跑模型；若另行執行，輸出只可報告狀態、計數、延遲與固定 reason code，不得保存 page image、完整 OCR/model text 或 raw request/response。

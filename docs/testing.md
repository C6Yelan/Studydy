# 測試

## Student flow browser regression

此 regression 的 core full-stack case 不攔截產品 API，會用 harness 產生的安全 PDF 經真 development backend 建立 material、run、Knowledge Map、assessment 與 Learning State，並驗證 backend restart recovery。其餘 `page.route` 只用於代表性的 failure／partial injection；這些案例不宣稱為 full-stack。

先從 `frontend/` 安裝前端套件與 Playwright Chromium：

```bash
npm ci
npm run e2e:install
```

再依作業系統慣例啟用 `backend` 的虛擬環境，確認 `python` 指向該環境，並從 repository root 執行：

```bash
python backend/tests/runtime/study_flow_e2e_runner.py --list
python backend/tests/runtime/study_flow_e2e_runner.py
```

Python runner 會確認固定 port `4173`、`8001` 未被占用，建立 disposable PostgreSQL，套用 migration，再啟動自己擁有的 Uvicorn、Vite 與 Playwright child。成功、失敗、timeout 或 signal 都只清理這次建立的 process group 與 PostgreSQL container。若直接執行 inner `playwright test`，因缺少 harness identity 會以 `E2E_HARNESS_REQUIRED` 結束。

失敗時 trace 與 screenshot 位於 ignored `test-results/`，HTML report 位於 ignored `playwright-report/`；不得提交這些 runtime artifacts。Chromium 由標準 Playwright cache 管理，可用 `npm run e2e:install` 安裝。

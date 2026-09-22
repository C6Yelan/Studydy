# Testing and qualification

## Local regression

教材分析結果的正式檢核發布、既有教材重整與離線檢查見 [material-review.md](material-review.md)。
既有教材重整使用已保存的 JSON 與來源；新教材與追加來源則在原始分析後執行複核。
程式回歸使用受控模型回應，與另行授權的真實模型品質實測分開記錄。

From the repository root:

```bash
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests
PYTHONPATH=local_ai/src backend/.venv/bin/pytest -q local_ai/tests
cd frontend
npm test
npm run typecheck
npm run build
cd ..
backend/.venv/bin/python backend/tests/runtime/browser_e2e_runner.py
```

Backend tests create a pinned disposable PostgreSQL 18 container unless a private
`STUDYDY_TEST_POSTGRES_DSN` pointing at a dedicated `studydy_test*` control database is supplied.
They cover fresh installation and the additive credentials and material-name migrations, owner isolation, immutable Knowledge Structure, source-bound
Assessment, private answer, server-side scoring, append-only AnswerEvent, mastery, guidance,
idempotency, stale state, and the HTTP API closed loop.

Email credentials use `0004_email_credentials.sql`: old development credentials are cleared and old sessions revoked once; learner IDs and owned records remain. Auth tests cover normalized Email uniqueness, invalid syntax, generic login failures, hashing/session regressions and the new schema. No Email DNS lookup or model call is used.

The accepted Knowledge Structure v2 schema can be upgraded with `0002_learner_credentials.sql`;
existing learner IDs and owners remain unchanged. `0003_material_display_name.sql` adds names.
Migration tests upgrade the accepted schema with saved synthetic records, verify the records and
old upload receipts remain readable, and verify a repeat migration is a no-op. Databases
from before the accepted initial-schema checksum still require a separate migration decision.

The standalone browser runner starts only a disposable Vite process. Its API fixtures use the final public
contract and verify Document Tree layout, the five Relation styles/reason interaction, Evidence
locator, StudySession, Assessment, and feedback. Real model behavior is qualified separately.

## Account regression (local only)

Build the frontend first, then run:

```bash
npm --prefix frontend run build
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests/runtime
```

`test_account_browser.py` reserves local API port 8001 and frontend port 4173, starts the production
frontend with Vite preview, and uses real account endpoints and a disposable PostgreSQL database.
Keep those ports free. The fixture uses synthetic saved material and disables model preflight and
worker startup only inside the test; it does not load models or start a cloud pod. The browser
registers B, logs A out, tests private PDF/Map denial, and logs A in from a separate browser context
without copying cookies or browser storage. This proves account behavior, not model quality or the
later full restart/resume qualification.

## Material library regression (local only)

The same runtime command includes `test_material_library.py` and `test_material_library_browser.py`.
The latter uses the shared local API/Vite fixture with a production build and real PostgreSQL.
It verifies fresh browser login through the server-backed library, named and unfinished materials,
prior succeeded/partial results after a newer failure, exact version reopen, PDF reads and account
isolation. Product-table snapshots must remain identical and backend model HTTP calls must remain
zero. It does not test or implement learning-history restoration.

The standalone mocked browser suite also tests library loading/error/empty states and rejects a
Map route whose processing run points at another revision.

## Learning resume regression (local only)

`runtime/test_learning_resume.py` 驗證題組 resume v4 的唯讀性與 owner／版本綁定。
`test_assessment_sets_browser.py`、`test_assessment_remediation_browser.py` 與
`test_concept_navigation_browser.py` 驗證整組交卷、回應遺失、補強、重新登入與跨觀念接續。
模型回應由隔離 fixture 提供，不呼叫真實模型。舊單題 API／書籤與相容測試已移除。
`runtime/test_assessment_quality.py` 改用題組 worker 檢查品質排序、重複題與目前 provenance；
不是實際模型品質驗收。

## 單一觀念題組回歸

現行契約見 [assessment-sets.md](assessment-sets.md)。`runtime/test_assessment_sets.py` 驗證
動態題數、交易／lease、成員私密性、部分發布、失敗重試、併發與取消／刪除。
`runtime/test_assessment_sets_browser.py` 以真 API／隔離 DB 在桌機與手機驗證完整題組。
舊單題生成、作答、恢復與 guidance API／UI 已移除；`product-cutover.spec.ts` 保留現行地圖與題組入口回歸，
`study-layout.spec.ts` 驗證 inline preparing、來源、resume 與無取消入口。合成 fixture 不算真實模型品質證據。

日常 frontend 使用 `frontend/dist`，測試不要覆寫它。改用獨立 build：

```bash
npm --prefix frontend run build -- --outDir ../.studydy-runtime/b05d-frontend --emptyOutDir
STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/b05d-frontend" \
STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002 \
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q \
  backend/tests/runtime/test_assessment_sets.py backend/tests/runtime/test_assessment_sets_browser.py
```

## 錯題補強回歸

B5-R 契約見 [assessment-remediation.md](assessment-remediation.md)。核心為
`runtime/test_assessment_remediation.py`，真 API／DB browser 為
`runtime/test_assessment_remediation_browser.py`，另保留題組與已存題目的恢復回歸。
初篩／補強不得重算舊答案，複習及 GET 不增加 AnswerEvent，補強正確不補足獨立掌握證據。
測試 build 使用獨立 `.studydy-runtime/b05r-frontend`，以 `STUDYDY_E2E_FRONTEND_DIST`
傳給 browser runner，保持日常 `frontend/dist` 穩定；測試 ports 4183／8002。

## 整組交卷與版面

`runtime/test_assessment_set_submission.py` 覆蓋整組原子提交、少答／錯誤成員全組拒絕、
中途 DB 保存失敗回滾、並行／重播不重複評分，以及保留切換前已存的個別答案。
`test_assessment_sets_browser.py` 改為整組交卷與回應遺失查回，在 1920／1536／1366／390
驗證卡片寬度／垂直排列、單一交卷按鈕、交卷前不顯示正誤及新登入恢復；補強 browser
同時改用整組交卷。獨立 build 為 `.studydy-runtime/b05-batch-frontend`，不覆寫日常 bundle。

## 學習導覽捲動回歸

`product-cutover.spec.ts` 的 `learning navigator scrolls` 使用 100 個合成概念，
在桌機／手機寬度實際送出滾輪事件，驗證清單可捲至最末項、畫布縮放不受影響、
重新開啟仍能看到選中項，以及鍵盤聚焦能回到首項。不能只靠 Playwright 自動
`scrollIntoView` 後點到末項來宣稱捲動正常；外框被裁切而清單沒有高度限制時，
後者仍可能通過。本次修正以 flex 將外框高度傳到清單，4 個導覽／搜尋案例通過。

## 跨觀念題組接續

`runtime/test_assessment_concept_navigation.py` 驗證 A 生成中切到 B、不同觀念各自建立及交卷、
同一觀念防重複，以及 B 不封鎖 A 的複習／補強／失敗重試。
`runtime/test_concept_navigation_browser.py` 以真 API／隔離 DB 在桌機與手機操作 A → B → A，
確認另一視窗已建立同觀念題組時會接續它。整組交卷 browser 的 1536 案例另注入版本前進，
驗證明確 409 後保留選取、改用最新版本，再遇回應遺失仍只保存一份答案。

## Runtime verification

`runtime/test_checkpoint_cleanup.py` 以隔離 PostgreSQL 驗證發布後清理，包括
`partial/needs_review`、未發布失敗保留、清理失敗補做與晚到 worker 禁止重建 checkpoint。
配合 `test_source_revisions.py` 的接續案例及 `test_material_full_delete.py` 檢查清理範圍；
清理以已 commit 的發布結果為準，不以模型完成或品質旗標決定。

`runtime/test_material_runtime.py` 驗證出題設定改變後，pending 工作可用原設定完成，
failed checkpoint 可在新重試中沿用且不增加已完成部分的模型呼叫；教材 prompt、
實際模型改變或設定缺失／損毀時停止。migration 0010 新增 `runtime_lock_document`，
既有欄位與 hashes 保持不變；新舊資料統一走相同的教材設定比對流程。

B3-A／B3-B 的核心案例在 `test_source_revisions.py`、`test_source_identity.py` 與
`test_source_revision_migration.py`；真 API/DB browser 在 `test_source_revisions_browser.py`。
後者仍使用受控語意回應，不是模型品質驗收。可用
`STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002` 避開產品 ports。
瀏覽器請求次數／版面驗收以 `browser_e2e_runner.main(..., production=True)` 檢查正式建置；
Vite dev 的 React StrictMode 可能重複唯讀 GET，不應誤判為搜尋觸發額外寫入。
多個 spec 的 runner 可明確指定 `timeout_seconds`，不變更 individual test timeout 或重試次數。
最新功能契約見 [source-revisions.md](source-revisions.md)。

B3-B 初次多檔 browser 使用 `upload.spec.ts` 驗證逐檔驗證、部分失敗重試、順序、回應遺失與明確移除；
`initial-sources-real.spec.ts` 由真 API／隔離 DB／worker fixture 執行 PDF＋TXT＋Markdown 的初次建立及逐來源回查。
Upload 已統一走來源確認頁；舊單 PDF 直接建立 run 的 UI 測試已依新操作契約替換。

失敗恢復必測 `test_source_revisions.py` 的 fault injection：同一頁第二批失敗，只重試該批；
最後組裝／發布失敗，重試不建立模型 client、不執行 preflight／OCR／語意呼叫；
保存失敗須在推論前停止，損毀 checkpoint 不得靜默全量重跑。`test_knowledge_structure_v1.py`
另覆蓋跨批主名稱／別名互換，以及不同模型 key 產生相同 canonical Concept 的情況。

The runtime root contains only the Python 3.12 OCR environment and Unlimited-OCR model. Gemma 4 is
already resident at `127.0.0.1:18000`:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m runtime.local_runtime verify
```

Success means the OCR model loads once and closes cleanly, while the existing Gemma 4 service passes
health, vLLM version, served-model, 32K context, and tokenizer checks. No verifier or second model
lifecycle is loaded.

## A40 final qualification

The active primary input is the approved 45-page C array/string PDF. The runner verifies its exact
source SHA and page count; there is no default additional textbook or 8-page benchmark rerun.
The current source SHA-256 is `07b1c1c1352934f75cc5182aa15db8a702138861f7557f470f9200ac33b06d13`,
matching competition final. The CLI `run` command below is a same-host A40 diagnostic.
For local OCR plus remote Gemma, use the normal product upload/worker pipeline and
collect its artifact and remote runtime observations before applying the same `score` gate.
Use a fresh private output directory under ignored `.studydy-runtime/`, or a mode-0700 directory
named `/tmp/studydy-*` when the network filesystem cannot preserve Unix permissions.

```bash
PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/a40_final_qualification.py run \
  --array '<APPROVED_45_PAGE_PDF>' --output '<NEW_PRIVATE_OUTPUT>'

PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/a40_final_qualification.py score \
  --review '<PRIVATE_REVIEW_JSON>' --output '<PRIVATE_OUTPUT>'
```

The v2 review records explicit reviewed/usable counts and known limitations. Semantic acceptance
uses 85%; source/revision binding, complete canonical Path, truthful failure, private-answer safety,
zero observed false mastery, and runtime liveness remain required. The scorer does not invent
literal-fidelity percentages or enforce the retired 8-page/180-second timing gate.

The CLI material run is a diagnostic path. Final product acceptance also needs the real browser/API
loop: upload, progress, Map/Path, source PDF locator, Assessment/Answer, guidance, and reload/reopen.
Store that evidence privately and bind its manual review to the exact artifact revision. Never mark
unexecuted browser checks true in the review example.

## Auth UX regression

```bash
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests/runtime/test_accounts.py backend/tests/runtime/test_account_browser.py
```

The account browser suite covers Email/password-manager semantics, no native validation bubbles,
inline errors/focus/correction, password reveal, duplicate-submit protection, safe API errors,
Login/Register navigation and real owner/session isolation. Both pages are checked at
1536×1024, 1920×1080 and 390×844 for size, centering, overflow and screenshots. Screenshots are
written only to the ignored browser test output directory. No OAuth or verification flow is tested or implemented.

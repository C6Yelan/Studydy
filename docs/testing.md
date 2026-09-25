# Testing and qualification

## Local regression

教材分析結果的正式檢核發布、既有教材重整與離線檢查見 [material-review.md](material-review.md)。
既有教材重整使用已保存的 JSON 與來源；新教材與追加來源則在原始分析後執行複核。
程式回歸使用受控模型回應，與另行授權的真實模型品質實測分開記錄。

From the repository root:

```bash
export STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/backend-check-frontend"
export STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002
npm --prefix frontend run build -- --outDir "$STUDYDY_E2E_FRONTEND_DIST" --emptyOutDir
env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
  PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests
PYTHONPATH=local_ai/src backend/.venv/bin/pytest -q local_ai/tests
npm --prefix frontend test
backend/.venv/bin/python backend/tests/runtime/browser_e2e_runner.py
```

獨立 build 同時執行 TypeScript 檢查，不覆寫日常 `frontend/dist`；以下 browser 命令
沿用上方 build 與 ports。來源轉檔使用後端共用環境，不再需要獨立 normalizer fixture。

2026-09-25 整理收尾：清除 6 個已失效的 normalizer fixture 匯入及 2 處參數後，完整後端
**369 passed、0 skipped（310.36 秒）**，含表層 159、runtime 210（其中 10 個 browser fixture）；
唯一 warning 為既有 Starlette TestClient 的 httpx 棄用提示。local AI **13 passed**、
逐檔 Node **51 passed**，TypeScript／獨立 build 通過（既有 bundle 大小提示）。
使用 disposable PostgreSQL、合成來源與 4183／8002，未使用產品 DB／PDF store 或真實模型；
執行期間後端 source／tests／lock 未變動。本輪未重跑完整獨立前端 E2E，不擴張為模型品質結論。

Backend tests create a pinned disposable PostgreSQL 18 container unless a private
`STUDYDY_TEST_POSTGRES_DSN` pointing at a dedicated `studydy_test*` control database is supplied.
They cover the four domain baselines, owner isolation, immutable Knowledge Structure, source-bound
Assessment, private answers, server-side scoring, append-only AnswerEvent, mastery, guidance,
idempotency, stale state, and the HTTP API closed loop.

`runtime/infrastructure/test_migrations.py` verifies fresh installation, repeat no-op with saved credentials/sessions,
ledger checksum/sequence rejection, concurrent installation, and transactional rollback/retry of the
next migration. Old development-step upgrade tests have been removed. Auth tests retain normalized
Email uniqueness, syntax validation, hashing, session safety and owner isolation; no DNS or model call is used.

Migration 測試驗證安裝、資料保留與失敗回滾；不再重抄資料表／欄位／trigger 名稱清單。
來源不可變、題組 scope 與補強 origin 約束由對應功能測試實際嘗試修改來驗證。

The source-collection baseline also verifies that a draft can have no representative PDF, draft
creation remains idempotent, the library omits the retired ingestion marker, and the database rejects
the retired `source_pdf` artifact kind. `seed_pdf()` continues to exercise the current source workflow.

契約 v1 回歸另外驗證：內部分析草稿不能發布、正式 KS 使用現行 v1、
DB 拒絕缺少 schema 的 JSON、所有產品路由唯一且位於 `/v1/`。來源 API 測試涵蓋原檔下載與
normalized PDF 預覽的不同 MIME／標頭、owner 隔離、錯誤 artifact 類型及退役 `/v2` 路由。

`runtime/infrastructure/test_validation_boundaries.py` 使用合成資料檢查不同教材／出題模型各自綁定執行快照，
重算內容 hash 仍不能偽造另一個模型的 provenance；讀地圖／題組／進度不開來源檔，
Evidence locator 只驗證所用 mapping。三種來源檔損毀均在使用與發布邊界拒絕；prior 題目
只完整驗證一次，同一題組設定於交易內共用。語意服務測試以 MockTransport 驗證設定中的模型
被實際送入請求，discovery 回傳不同模型仍拒絕。這些是程式契約測試，不是新模型品質驗收。

Binding 與設定快照不一致的檢查集中在 `test_runtime_boundaries_v1.py`，不額外建立 DB／PDF
重測同一個純函式。題目 schema 與偽造 provenance 共用一組真實題組 fixture；資料庫 KS 約束
保留「缺少 schema」的資料庫失敗條件。HTTP 四種 task 共用請求契約案例，
仍分別驗證各 task 的 budget、schema、tokenizer 與生成設定。

The 2026-09-23 baseline adoption compares the old final schema with a fresh baseline in isolated
PostgreSQL, rehearses backup restore and ledger adoption/rollback, and checks all product-table
contents and artifact files before/after. This is a one-time local cutover, not an automatic path in
the migration runner. Historical SQL is available in Git and the private cutover backup.

The standalone browser runner starts only a disposable Vite preview process. Its API fixtures use the final public
contract and verify Document Tree layout, the five Relation styles/reason interaction, Evidence
locator, StudySession, Assessment, and feedback. Real model behavior is qualified separately.

### Runtime 案例精簡（2026-09-25）

六個領域的 runtime 收集數由 223 減為 211，沒有把參數搬進迴圈來壓低數字。
本次依現行斷言、產品分支及其他保留案例刪除以下 12 個重複樣本：

| 案例 | 前 → 後 | 刪減依據與保留範圍 |
| --- | --- | --- |
| 題組整組交卷 browser 寬度 | 4 → 2 | 移除 1920、1366；兩者與 1536 都執行 ≥1280px 的桌機雙欄／側欄分支及相同完整流程。1536 另測 409 版本衝突後重送，390 測手機單欄與直接交卷回應遺失；兩者都驗證重新登入恢復。 |
| 動態題數 | 3 → 2 | 移除中間值 3，保留 1、7 的題數、逐點發布、私密性與唯讀斷言；三題正常發布仍由 `test_api_preserves_scope_private_preparation_and_read_only_resume`、整組交卷及 browser 覆蓋。 |
| 改名無效名稱 | 9 → 5 | 移除空字串、NUL、Tab、DEL；空白經 strip 覆蓋空值，換行覆蓋先拒絕 Cc 再 strip，另留非 ASCII Cc、Cs surrogate 與 201 字超長。新增 storage sentinel，確保拒絕發生於存取 DB 前。 |
| 帳號無效 Email | 7 → 2 | 移除 `a@`、`a b@example.com`、`a..b@example.com`、`a@example..com`、`a@localhost`；語法交給 `EmailStr`，保留空值與非空錯誤，兩者都驗證註冊／登入轉成 `REQUEST_INVALID` 且不存取 DB。正規化、唯一性與不查 DNS 的整合案例保留。 |

密碼上下限案例原本使用無效 Email `learner`，會先在 Email 驗證失敗；改成有效 Email
並加入 storage sentinel，讓保留的兩個案例確實驗證密碼限制。

權限隔離、整組交卷各種無效成員／中途回滾、教材刪除的競爭與恢復、來源綁定、
checkpoint／晚到 worker、migration 各失敗原因均保留。舊 scrypt 升級、已存個別答案
及已保存分析回應仍有現行讀取／恢復契約；拒絕舊 schema／路由也在保護現行邊界，
不能只因名稱提到舊版而刪除。本次未找到有足夠證據可整項移除的過時契約。

限制：不再逐次驗證 1920／1366 題組版面及上述每個非法字串，也未新增 760～1279px
中間版面的覆蓋；211 不是已證明的全域最小集合。合成 fixture 只提供程式回歸證據，
不代表真實模型品質。瀏覽器驗證使用下述獨立 build 與 4183／8002，DB 與 artifact 均隔離。

本次完整 runtime 結果為 **211 passed、0 skipped，305.45 秒**，含 10 個 browser fixture
案例；唯一 warning 是既有 Starlette TestClient 的 httpx 棄用提示。獨立 frontend build
成功（保留既有 bundle 大小提示）。首次沙箱執行因 Docker 存取限制未完成，放行後以
disposable PostgreSQL 完整重跑；沒有存取產品 DB／PDF store 或呼叫真實模型。
未重跑刪減前的完整套件，因此不宣稱實測節省多少時間。

```bash
npm --prefix frontend run build -- --outDir ../.studydy-runtime/runtime-prune-frontend --emptyOutDir
env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
  STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/runtime-prune-frontend" \
  STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002 \
  PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests/runtime --durations=15
```

### 表層單元測試精簡（2026-09-25）

檢視 `backend/tests/test_*.py` 全部 10 個檔案，收集案例由 164 減為 161：

| 刪減 | 保留的覆蓋與依據 |
| --- | --- |
| blind check 的 `none` 參數 | `test_false_safe_numeric_answer_to_type_question_is_not_published` 已用「數值不能回答型別問題」驗證同一拒絕條件；歧義、錯誤正解與重複題仍分別保留。 |
| source binding 的 `[]` 參數 | 與 null、非空字串都由 `isinstance(binding, dict)` 拒絕；保留後兩者，並先驗證未竄改文件有效，避免負向案例因其他前置錯誤通過。 |
| OCR 的空白文字＋空圖片組合 | 空白及無效幾何各保留一個全頁拒絕案例；`test_blank_and_image_only_blocks_are_rejected_but_text_page_remains` 仍驗證兩種 block 被濾除且有效文字保留。 |

另刪除 `safety=reject` 搭配不同 distractor 的重複片段：產品在讀取 distractor 前已拒絕，
不能將其稱為語意等價判斷。非 v1 schema 的同案例迴圈由 v2／v3／v4 縮為 v2，仍重算
revision 再驗證拒絕；以上兩項不改 pytest 案例數。其後依全新安裝的現行 v1 範圍，
移除專為 v2 建立的負向案例與舊 UTF-8 位元組限制案例。子程序退出測試改名為實際驗證的
「非零退出回報與重複 close」，原斷言並未驗證 request／response 大小限制或 stderr 隱私。

未改 token 預算邊界、四種 HTTP task、字面值／Relation 類別、審查補正、學習引導、
取消時機與 OCR 子程序生命週期。90 頁分批測試雖占本輪最多時間（約 3.1 秒），仍保留
大頁數、跨批目錄累積與 Evidence 不重複／不遺漏的覆蓋。

使用 `PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests/test_*.py --durations=10`
實跑刪減前 **164 passed（4.88 秒）**、刪減後 **161 passed（4.98 秒）**，無 skip／warning；
這次減少重複維護，未觀察到執行加速。不再逐值測上述樣本，亦未證明 161 是最小集合。
僅用合成資料、暫存檔、MockTransport 與測試子程序，不連 DB 或真實模型；未重跑 runtime／browser，
不新增模型品質結論。

### 全域測試退役檢查（2026-09-25）

範圍為正式版的 42 個 backend、2 個 local AI、6 個 Node 與 26 個 Playwright 測試檔。
以本次開始時的工作樹為準：backend 收集 369 個（表層 159、runtime 210），未再改動；
local AI 13 個未刪減。Node 案例 52 → 51，Playwright 收集 422 → 419（26 → 25 檔）。

| 移除項目 | 證據與保留範圍 |
| --- | --- |
| `source-revisions-real.spec.ts`（2 個） | 全專案已沒有程式設定 `STUDYDY_E2E_REVISION_REAL`／`STUDYDY_E2E_REVISION_MATERIAL`，現行 Python browser fixture 只啟動 `initial-sources-real.spec.ts`。移除無執行入口的舊 spec；這兩個原本即跳過，不能算成減少兩次實際 browser 執行。 |
| 地圖導覽的 `review_prerequisite`（1 個） | 後端 `_next_action` 不產生此 action；前置觀念提醒由 `assess` 與 `prerequisite_concept_ids` 表示。保留其他仍可由後端 reader 產生的 action；本次不改產品型別或 UI 殘留分支。 |
| `learningNavigationItems` 的 Path 外概念（1 個） | 唯一產品 consumer 接受已通過 API 驗證的地圖，Path 必須完整且每個概念恰好一次。移除繞過該邊界要求 helper 接受不合法資料的測試；API／browser 拒絕錯誤 Path 的測試仍保留。 |

同時修正首次焦點 fixture，使 cover、其餘概念與 Path 完整對應；未知 Relation 類型
測試先確認合法端點可讀，再只變更 type，避免原本的自連結先被拒絕造成假陽性。
兩個殘留單題／私密答案描述的 client 測試改名符合實際斷言，私密答案拒絕仍由題組測試保護。

未因名稱含 old／legacy 就刪除仍受支持的行為：`.doc`／`.ppt` 是現行輸入格式；
密碼雜湊升級、已存個別答案、`no_safe`／defer／resume reader 及
`SOURCE_UPDATE_NEEDS_REVIEW` 保存回應接續仍存在於產品程式或明定契約。
這些若要在全新安裝中一併退役，需連同產品讀寫／資料契約處理，不能只移除測試。
刪除、來源綁定、migration、checkpoint、取消／晚到 worker、字面值與 HTTP／OCR 邊界仍保留。

本次驗證：逐檔執行 Node 共 **51 passed**、local AI **13 passed**；獨立 frontend build
成功（既有 bundle 大小提示）。`product-cutover.spec.ts` 與 `source-revisions.spec.ts`
共 **85 passed（51.5 秒，0 skipped）**，使用 `.studydy-runtime/obsolete-tests-frontend`
與 4183／8002，API 全為合成 fixture。`npm test` 的檔案層級摘要是 6，與個別案例數分開記錄。
backend 本次僅收集，未重跑 DB runtime，也未執行其餘 browser specs。

覆蓋限制：真 API／DB 的「追加來源後從 UI 接續舊學習」目前沒有接通的 browser fixture；
保留的來源追加後端回歸、mock browser 與初次多來源真 API browser 不能合稱已驗證該完整流程。
本次未存取產品 DB／PDF store、未呼叫真實模型，亦不宣稱剩餘測試已是全域最小集合。

## Account regression (local only)

先完成上方獨立 build 並設定測試 ports，再執行：

```bash
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests/runtime
```

`test_account_browser.py` uses the configured test ports (API 8002 and frontend 4183 above), starts the production
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

`runtime/study/test_persistent_study_state.py` 驗證題組 resume v1 的唯讀性與 owner／版本綁定。
`test_assessment_sets_browser.py`、`test_assessment_remediation_browser.py` 與
`test_concept_navigation_browser.py` 驗證整組交卷、回應遺失、補強、重新登入與跨觀念接續。
模型回應由隔離 fixture 提供，不呼叫真實模型。舊單題 API／書籤與相容測試已移除。
`runtime/assessments/test_assessment_quality.py` 改用題組 worker 檢查品質排序、重複題與目前 provenance；
不是實際模型品質驗收。

## 單一觀念題組回歸

現行契約見 [assessment-sets.md](assessment-sets.md)。`runtime/assessments/test_assessment_sets.py` 驗證
動態題數、交易／lease、成員私密性、部分發布、失敗重試、併發與取消／刪除。
`runtime/assessments/test_assessment_sets_browser.py` 以真 API／隔離 DB 在桌機與手機驗證完整題組。
舊單題生成、作答、恢復與 guidance API／UI 已移除；`product-cutover.spec.ts` 保留現行地圖與題組入口回歸，
`study-layout.spec.ts` 驗證 inline preparing、來源、resume 與無取消入口。合成 fixture 不算真實模型品質證據。

日常 frontend 使用 `frontend/dist`，測試不要覆寫它。改用獨立 build：

```bash
npm --prefix frontend run build -- --outDir ../.studydy-runtime/b05d-frontend --emptyOutDir
STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/b05d-frontend" \
STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002 \
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q \
  backend/tests/runtime/assessments/test_assessment_sets.py backend/tests/runtime/assessments/test_assessment_sets_browser.py
```

## 錯題補強回歸

B5-R 契約見 [assessment-remediation.md](assessment-remediation.md)。核心為
`runtime/assessments/test_assessment_remediation.py`，真 API／DB browser 為
`runtime/assessments/test_assessment_remediation_browser.py`，另保留題組與已存題目的恢復回歸。
初篩／補強不得重算舊答案，閱讀及 GET 不增加 AnswerEvent，補強正確不補足獨立掌握證據。
測試 build 使用獨立 `.studydy-runtime/b05r-frontend`，以 `STUDYDY_E2E_FRONTEND_DIST`
傳給 browser runner，保持日常 `frontend/dist` 穩定；測試 ports 4183／8002。

## 整組交卷與版面

`runtime/assessments/test_assessment_set_submission.py` 覆蓋整組原子提交、少答／錯誤成員全組拒絕、
中途 DB 保存失敗回滾、並行／重播不重複評分，以及保留切換前已存的個別答案。
`test_assessment_sets_browser.py` 改為整組交卷與回應遺失查回，在 1536／390
驗證卡片等寬、桌機雙欄／手機單欄、單一交卷按鈕、交卷前不顯示正誤及新登入恢復；補強 browser
同時改用整組交卷。獨立 build 為 `.studydy-runtime/b05-batch-frontend`，不覆寫日常 bundle。

## 學習導覽捲動回歸

`product-cutover.spec.ts` 的基本地圖矩陣使用 5 組 viewport／資料量配對，保留
1920／1536／1366／390 寬度及 1／20／100／200 個概念；桌機與手機各保留鍵盤詳情操作。
大集合的該 fixture 都呈現相同的兩跳鄰近節點，因此不重跑 16 組全排列。
密集圖顯示上限、大圖拖曳、縮放／reflow 與長清單捲動仍由各自的瀏覽器案例驗證。

`product-cutover.spec.ts` 的 `learning navigator scrolls` 使用 100 個合成概念，
在桌機／手機寬度實際送出滾輪事件，驗證清單可捲至最末項、畫布縮放不受影響、
重新開啟仍能看到選中項，以及鍵盤聚焦能回到首項。不能只靠 Playwright 自動
`scrollIntoView` 後點到末項來宣稱捲動正常；外框被裁切而清單沒有高度限制時，
後者仍可能通過。本次修正以 flex 將外框高度傳到清單，4 個導覽／搜尋案例通過。

## 跨觀念題組接續

`runtime/assessments/test_assessment_concept_navigation.py` 驗證 A 生成中切到 B、不同觀念各自建立及交卷、
同一觀念防重複，以及 B 不封鎖 A 的補強／失敗重試。
`runtime/assessments/test_concept_navigation_browser.py` 以真 API／隔離 DB 在桌機與手機操作 A → B → A，
確認另一視窗已建立同觀念題組時會接續它。整組交卷 browser 的 1536 案例另注入版本前進，
驗證明確 409 後保留選取、改用最新版本，再遇回應遺失仍只保存一份答案。

## Runtime verification

`runtime/infrastructure/test_runtime_boundaries_v1.py` 以受控儲存等待驗證移除來源與 PDF 預覽不阻塞
同一 event loop 的其他 API 請求，並確認空 body 驗證仍在儲存操作前拒絕額外內容。
Runtime 設定／安裝驗證從 `material_runtime.py` 測試，來源定位則從
`storage/knowledge_structures.py` 進入；既有來源、發布與接續測試保留原行為斷言。

鎖與等待的回歸：`test_material_pipeline_v1.py` 驗證原生文字分析不取 OCR 鎖、
sidecar 存活期間保持互斥且在語意推論前釋放；`test_local_ai_process_v1.py` 驗證
OCR 結束時忽略 EOF 的子程序會被回收。Gemma 與 OCR 推論本身仍保留原有無時間上限政策。
`runtime/materials/test_artifact_store.py` 驗證清理跳過尚未提交的寫入／刪除，後續依 DB commit 或
rollback 保留、移除或還原檔案，以及 DB 等鎖逾時會完整回滾、後續交易仍可用。
`runtime/materials/test_material_discard.py` 驗證教材刪除的生命週期、競爭、失敗補做及 API 約束；
完整學習資料清理見 `runtime/materials/test_material_full_delete.py`。
產品 ORM 連線等待 5 秒、取鎖等待 5 秒、單一 SQL 60 秒；不設定 idle transaction 逾時，
避免檔案尚在寫入時 DB 提前釋放保護鎖。checkpoint 的 hash／序列化在取鎖前完成，
寫檔與發布仍保留交易保護。
`runtime/infrastructure/test_runtime_boundaries_v1.py` 驗證啟動等待 5 秒、停止等待 10 秒後如實回報失敗，
晚到 worker 不再領取下一份工作。停止逾時不代表目前工作已停止；程序退出後，
未完成工作依既有 lease／checkpoint 恢復。`test_source_revisions.py` 與
`test_assessment_sets.py` 另驗證 heartbeat 遇短暫儲存失敗會續試，失效工作仍停止。

`runtime/sources/test_checkpoint_cleanup.py` 以隔離 PostgreSQL 驗證發布後清理，包括
`partial/needs_review`、未發布失敗保留、清理失敗補做與晚到 worker 禁止重建 checkpoint。
配合 `test_source_revisions.py` 的接續案例及 `test_material_full_delete.py` 檢查清理範圍；
清理以已 commit 的發布結果為準，不以模型完成或品質旗標決定。

`runtime/sources/test_material_runtime.py` 驗證出題設定改變後，pending 工作可用原設定完成，
failed checkpoint 可在新重試中沿用且不增加已完成部分的模型呼叫；教材 prompt、
實際模型改變或設定缺失／損毀時停止。`0002_sources_and_processing.sql` 包含 `runtime_lock_document`，
既有欄位與 hashes 保持不變；新舊資料統一走相同的教材設定比對流程。

B3-A／B3-B 的核心案例在 `test_source_revisions.py`、`test_source_identity.py` 與
`test_migrations.py`；
真 API/DB browser 在 `test_source_revisions_browser.py`。
後者仍使用受控語意回應，不是模型品質驗收。可用
`STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002` 避開產品 ports。
瀏覽器請求次數／版面驗收由 `browser_e2e_runner.main(...)` 預覽正式建置；
Vite dev 的 React StrictMode 可能重複唯讀 GET，不適合作為此 runner 的驗收結果。
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

## Real-model qualification

Current model quality must be checked through the product upload, worker, and browser/API flow
against the model and runtime in `local_ai/runtime-lock.json`. Record the exact source and artifact
revision, reviewed/usable counts, limitations, source locators, Assessment/Answer results, and runtime
failures privately. The material review gate remains 85%; source/revision binding, complete canonical
Path, truthful failure, private-answer safety, zero observed false mastery, and runtime liveness are
also required. Do not count fixture tests or unexecuted browser steps as real-model evidence.

The [Gemma qualification record](gemma-runtime-qualification.md) describes the historical A40 run
and its actual limitations. Its hardware choice is not a requirement for the current local OCR plus
remote semantic service.

## Auth UX regression

沿用上方獨立 build 與測試 ports：

```bash
npm --prefix frontend test
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q backend/tests/runtime/accounts/test_accounts.py backend/tests/runtime/accounts/test_account_browser.py
```

The account browser suite covers Email/password-manager semantics, no native validation bubbles,
inline errors/focus/correction, password reveal, duplicate-submit protection, safe API errors,
Login/Register navigation and real owner/session isolation. Both pages are checked at
1536×1024, 1920×1080 and 390×844 for size, centering, overflow and screenshots. Screenshots are
written only to the ignored browser test output directory. No OAuth or verification flow is tested or implemented.

`test_migrations.py` 驗證最終 baseline 與 migration 保護；`test_assessment_remediation.py` 與對應 browser 驗證直接補強、再次答錯、冪等與 assisted semantics。

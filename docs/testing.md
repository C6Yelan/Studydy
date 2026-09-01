# 測試

## Material review browser regression

此 regression 不攔截產品 API，會用 harness 產生的安全 PDF 經真 local backend 建立 material 與 persisted `material_processing_run`，並驗證 API、資料庫與 frontend wiring。OCR、Concept 與 Knowledge Map producer 使用 deterministic test implementation，因此不宣稱驗證真實 OCR、Qwen 或其他 local AI；真 local AI 必須另外執行下方測試與 production smoke。

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
的操作，只 reconcile 已安裝 Studydy Python package 中的五個檔案：
`__init__.py`、`protocol.py`、`ocr_process.py`、`equivalence_process.py` 與
`assessment_process.py`。若有變更，會先保留五個檔案的完整 backup；`sync --rollback`
會從該 backup 還原。sync 不會在啟動時自動執行，也不會
進行下載、安裝或網路操作；在真實主機上執行會改動檔案，必須另行取得批准。

測試中的 disposable fixtures 只驗證這些 layout、驗證與 backup/rollback 邏輯。
真實主機 E2E 與 unseen-PDF 評估是另外核准的操作，不屬於 fixture 測試。

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

## Assessment generation qualification

Phase 06 的獨立 Assessment runtime binding、deterministic correctness gates、risk-only
repair、publication-independent private novelty qualification、mastery，以及單一 learner
progress/guidance contract可用以下命令驗證：

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m runtime.local_runtime verify
PYTHONPATH=backend/src backend/.venv/bin/python -c \
  'from runtime.storage.migrations import run_migrations; print(run_migrations())'
PYTHONPATH=backend/src:local_ai/src backend/.venv/bin/python -m pytest \
  backend/tests/test_assessment_generation.py \
  backend/tests/test_assessment_model_api.py \
  backend/tests/test_assessment_runtime.py \
  backend/tests/test_local_ai_process.py \
  local_ai/tests/test_protocol.py \
  local_ai/tests/test_assessment_process.py \
  backend/tests/runtime/test_assessment_items.py \
  backend/tests/runtime/test_answer_events.py \
  backend/tests/runtime/test_learning_states.py \
  backend/tests/runtime/test_learner_progress.py \
  backend/tests/runtime/test_api_runtime.py
```

Material / Agent 1–3 只由 `local_ai/runtime-lock.json` 綁定；Agent 4 的 prompt、
threshold、verifier protocol、selection policy 與 code hashes只存在
`local_ai/assessment-runtime-lock.json`。Assessment可共用同一實體Qwen與mDeBERTa安裝，
但 provenance必須保存`assessment-generation-runtime-binding/v1`的hash，不能保存
formal material binding。mDeBERTa會在inference前以`truncation=False`計算完整
Evidence-option pair；超過384 tokens時回傳明確reject，禁止以截斷內容promotion。
每個candidate先以selected Evidence subset驗證correct-option relative margin，再以full
Claim Evidence驗證整體margin與multiple-supported distractor risk；private
`assessment-generation-provenance/v2`保存兩組對齊分數，model宣告的support IDs不能單獨
作為grounding證明。
Novelty qualification不是publication gate。`question_id`綁定公開 Assessment內容以及
Map、Concept、Claim、Evidence、question type與policy等domain bindings；private semantic
identity由normalized student-visible prompt與private correct answer的semantic focus建立。
exact identity在整個StudySession防重，資料庫另以
`(study_session_id, semantic_identity)`唯一約束作為最後防線；semantic novelty只與同一
target Claim的prior artifacts比較。沒有prior或positively verified distinct才計入private
distinct mastery evidence。neutral、uncertain、timeout、invalid、unavailable、unsupported
或over-limit仍發布correctness-safe grounded nonduplicate，但保持unqualified。
同一StudySession依margin與candidate index deterministic選擇尚未儲存的semantic
identity；最高排名risky proposal的repair pool耗盡後，仍須繼續掃描較低排名的
unused safe proposals。只有correctness-safe、grounded、nonduplicate possibilities真正耗盡
時才記錄該Claim的no-safe狀態；novelty不確定或novelty-stage failure不得寫入no-safe。
耗盡時沿用no-safe handoff
記錄該claim的no-safe狀態，回傳`NO_SAFE_ASSESSMENT`／`ASSESSMENT_NO_NEW_SAFE_ITEM`，
再由`learner-progress/v1`的canonical `initial_learning_path` defer/resume guidance尋找下一個
可行重點；不建立新的Assessment
或AnswerEvent，也不推測答案或降低任何安全gate。

單一Claim mastery必須有兩個unique、correct、positively qualified item identities。
unqualified answers仍更新attempts、latest result、coverage、repeated errors與improvement
history，但不增加`qualified_distinct_correct_items`。`GET /progress`回傳同一
`event_watermark`下的`learner-progress/v1`；`POST /guidance/apply`只接受exact
`guidance_revision`並回傳重新推導的progress。舊context、learning-state、weakness、
adaptive-plan與suggestion derived APIs沒有alias或compatibility surface。Map v11 flat
grounded Tree與canonical inline `initial_learning_path`只讀；Agent 4不消費或推論
prerequisite、prerequisite-gap或Relation資料。

正式 `/v1` app 會在第一次 Assessment request lazy 啟動 Qwen 與 Assessment verifier，
後續 request 在同一安全 lifecycle 內 reuse ready process。reuse期間沿用既有
`material-analysis.lock`，因此 material worker只會等待、不會同時載入另一組模型；60秒
idle或app shutdown會回收process並釋放lock。任一 generation failure會同時丟棄Qwen與
verifier，下一次request重新cold start，不reuse可能損壞的process。這個lifecycle不改
Assessment runtime lock、prompt、NLI threshold、repair、Evidence Gate或selection policy。
fresh disposable database 應由上述 migration command 套用連續的 1–17 版 migration；
再次執行必須回傳空 tuple 並驗證 ledger checksum。`runtime.local_runtime verify`
必須同時驗證 assessment package source hashes、model revisions、prompt hashes 與
novelty/selection policy；lock 或 migration checksum 不符時應 fail closed。

真模型 qualification 必須從 Formal Concept / Claim / canonical exact Evidence 進入正式
prompt、validation、relative-margin selection、multiple-support risk repair 與 P06-02 builder；
不得以 PDF-to-question shortcut 或 schema-valid 取代語意人工評估。Gate 維持
representative safe promotion 至少 80%、representative 與 high-risk holdout critical
promotion 都為 0、multiple-supported challenges 觸發率 100%、unsafe stability flip 為 0，
且 P06-02 contract 100%。Golden、raw model output 與人工標註只能保存在 ignored 的
`.studydy-runtime/`，不得提交。

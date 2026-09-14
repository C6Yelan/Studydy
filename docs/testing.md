# Testing and qualification

## Local regression

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

The runtime suite includes `test_learning_resume.py` and `test_learning_resume_browser.py`.
The Browser test logs in from independent contexts, enters the existing session through the library,
reloads the same unanswered and answered questions, selects older sessions, and reads completed and
no-safe/deferred state. It forwards a real answer submission to the API, then aborts only its response;
“查回作答結果” must retrieve the committed AnswerEvent. Re-login and same-key replay must return that
same result. Each restore read keeps all seven product-table snapshots unchanged; the only writes
are the explicit answer submission and its idempotent replay, which produce one new AnswerEvent.
Backend model HTTP transport is blocked and must observe zero calls. These tests use controlled
saved fixtures; they do not perform the later workstation shutdown/restart or model qualification.

## Runtime verification

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

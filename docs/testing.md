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
They cover the single migration, owner isolation, immutable Knowledge Structure, source-bound
Assessment, private answer, server-side scoring, append-only AnswerEvent, mastery, guidance,
idempotency, stale state, and the HTTP API closed loop.

The resource-free Knowledge Structure v2 cutover requires a fresh pre-release database. Do not
apply the edited initial migration over a database with a different recorded checksum. Use a new
test database and artifact root; retain historical qualification outputs independently.

The browser runner starts only a disposable Vite process. Its API fixtures use the final public
contract and verify Document Tree layout, the five Relation styles/reason interaction, Evidence
locator, StudySession, Assessment, and feedback. Real model behavior is qualified separately.

## Runtime verification

The runtime root contains only the Python 3.12 OCR environment and Unlimited-OCR model. Qwen is
already resident at `127.0.0.1:8000`:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python -m runtime.local_runtime verify
```

Success means the OCR model loads once and closes cleanly, while the existing Qwen3.8 service passes
health, vLLM version, served-model, 32K context, and tokenizer checks. No verifier or second model
lifecycle is loaded.

## A40 final qualification

The active primary input is the approved 45-page C array/string PDF. The runner verifies its exact
source SHA and page count; there is no default additional textbook or 8-page benchmark rerun.
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

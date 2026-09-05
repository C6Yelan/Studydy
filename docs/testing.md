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

Run only on the approved A40 host, with the resident service and private runtime environment ready.
Inputs and outputs stay under ignored/private locations:

```bash
PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/a40_final_qualification.py run \
  --representative-eight '<PRIVATE_REPRESENTATIVE_8_PAGE_PDF>' \
  --array '<PRIVATE_45_PAGE_ARRAY_PDF>' \
  --technical '<PRIVATE_ADDITIONAL_TECHNICAL_PDF>' \
  --scanned '<PRIVATE_SCANNED_PDF>'

PYTHONPATH=backend/src backend/.venv/bin/python backend/scripts/a40_final_qualification.py score \
  --review '<PRIVATE_REVIEW_JSON>'
```

`run` rejects non-A40 hardware, a non-resident or reloaded Qwen, an 8-page runtime input outside the
1–3 call / 180-second semantic gates, a non-45-page primary input, a scanned input that never uses
OCR, invalid structures, unavailable VRAM telemetry, or service death. It saves full artifacts only
under ignored `.studydy-runtime/a40-final/` and prints aggregate identity only.

The bound `a40-final-review/v1` review records teaching-unit recall/disappearance/false merges,
duplicates, Relation type/direction/endpoints/reasons/prerequisites, the six focused Assessment safety
checks, the complete browser/API loop, OOM/engine-death/load evidence, warm timing, and the single
Python minor. `score` passes only at the published final gates and requires `mdeberta_decision` to be
`REMOVE`. Copy `backend/scripts/a40_final_review.example.json` into the ignored output directory and
fill it from human review; never edit the tracked example with private values.

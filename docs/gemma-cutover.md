# Semantic runtime cutover

Candidate baseline: `6180f13c869d7b7419d1d23538141cd729b24700`.
Branch: `be/feature-gemma4-cutover`; no merge.

## Before-change audit

The previous production model was Qwen/Qwen3.8-27B-FP8, revision
`017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`. These are historical identities,
not supported runtime alternatives.

| Category | Current dependencies found before editing |
|---|---|
| A — runtime contract | `local_ai/runtime-lock.json`, `backend/src/runtime/semantic_service.py`, `backend/src/pdf_evidence/material_pipeline.py`; local launchers under `ops/local/` also pin the old endpoint |
| B — provenance validators | `backend/src/knowledge_map/structure.py`, `backend/src/runtime/storage/knowledge_structures.py`, `backend/src/learning_adaptation/assessments.py` |
| C — tests/fixtures | `test_semantic_service_v1.py`, `test_assessment_safety_v1.py`, `test_knowledge_structure_v1.py`, `runtime/test_runtime_boundaries_v1.py` |
| D — current documentation | `docs/architecture.md`, `docs/testing.md`, `docs/runbook/A40_FINAL_WORKSTATION.md`, `docs/material-processing-cancellation.md`, `README.md`, `docs/local-environment.md` |
| E — historical references | This before-change audit; cancellation test history describes tests run before the cutover |

## Qualification authority

Read-only competition references used (relative to the private competition report root):

- `gemma4-31b-language-contract/01_PROMPT_DIFF.md`
- `gemma4-31b-language-contract/runtime-lock.gpt-oss.json`
- `gemma4-31b-full-final/01_RUNTIME.md`
- `gemma4-31b-full-final/runtime-lock.gpt-oss.json`
- `gemma4-31b-full-final/runs/full/runtime-lock.calibrated.json`
- `gemma4-31b-full-final/runtime-overlay/backend/src/runtime/semantic_service.py`
- `gemma4-31b-full-final/assessment_smoke.py`, `assessment/wire.json`
- `gemma4-31b-full-final/07_PRODUCT_QUALITY.md`, `08_ASSESSMENT_SMOKE.md`, `10_FINAL_DECISION.md`

The final loaded config and full-run config have identical SHA-256:
`5225a00745b7d5f8039553d2b0946693bd5b83235992f1f432cc90de382711b8`.
Historical filenames and unused preparation code are not runtime authority.
The competition loopback bridge address is deployment-specific; production uses
`http://127.0.0.1:18000` as requested.

## Acceptance limitation

Competition reported `GEMMA_COMPETITION_READY_WITH_LIMITATIONS`, with published
unsupported relations, one Claim scope error, and Assessment language/ambiguity
limitations. It replayed cached Evidence for all 45 pages, with zero OCR calls.
That does not establish a fresh OCR or formal-product qualification.
The current formal runbook requires 85% reviewed semantic usability and additional
product checks. These gates are not interchangeable; neither threshold is lowered.
Static fixture results do not establish live model quality.

## Contract changes

The sole semantic model is `google/gemma-4-31B-it-qat-w4a16-ct`, revision
`52f3f65bc7a02d555763bc923bd1d9094898219d`, on `http://127.0.0.1:18000`.
Context remains 32768 and concurrency remains one sequence. The qualified server
package versions match the previous lock: vLLM 0.28.0, Python 3.12, torch
2.13.0+cu130, CUDA 13.0, transformers 5.15.1. Stored runtime bindings now accept
only this exact model revision and package contract; no previous-model reader is added.
Runtime and document schemas are unchanged; this requires no database migration.

| Request | Before | Qualified Gemma |
|---|---|---|
| Material generation | temperature 1, top_p 0.95, top_k 20, min_p 0, presence_penalty 0, repetition_penalty 1; thinking true with the previous model's effort override | temperature 1, top_p 0.95, top_k 64; thinking true; no effort or penalty overrides |
| Material budget | 4096 output tokens | 8192 output tokens |
| Assessment generation | thinking false; 4096 tokens | temperature 1, top_p 0.95, top_k 64; thinking true; 4096 tokens |
| Assessment checker | temperature 0; thinking false; 1536 tokens | temperature 1, top_p 0.95, top_k 64; thinking true; 1536 tokens |

Material fresh-input budget 1536 and retry attempts 2 remain unchanged. Tokenizer
preflight/default kwargs port the qualified `enable_thinking: true` adaptation;
task-specific kwargs still override that default. Chat request formatting, JSON schemas,
Evidence checks, relation taxonomy, and Assessment prompt/checker
text are unchanged. The runtime lock's OCR block and local OCR packages are unchanged.

The exact material prompt diff is one newline followed by this qualified suffix:

```text
Use the primary language of the source material for all learner-facing generated text. For source material primarily written in Traditional Chinese, Concept labels, aliases, synthesized Claim meanings, and Relation learner-facing reasons must be written in Traditional Chinese. Do not translate or alter established technical terms, identifiers, function names, code, operators, formulas, escape sequences, numbers, or exact literals supported by Evidence.
```

Runtime-lock hashes (before → after):

- Product canonical SHA-256: `fdf1853648123dbfcb928f65b1086dd219bb826199b1c49cacf9d5fda8291f1b` → `3b3910d228b6fbc1cbf893462b387259f4568b9a0c5f52703671dbf0111020fb`.
- File SHA-256: `e0043b571352be774db74e2b088bdf11e4197729e84a34bbc9be6102bbd87bde` → `916f9be041892e82335c6fb02b44d67794da9879077cd8158fbf86fcac613301`.

The after-change search finds no old-model authority in production source or the
runtime lock. Intentionally retained historical mentions are this before-change audit
and the explicitly labelled pre-cutover test history in `material-processing-cancellation.md`.

## Static verification

- Backend unit/integration/browser fixtures: **239 passed** (89.11 seconds), using
  disposable PostgreSQL and synthetic PDFs, with no model calls.
- Final tokenizer adaptation and runtime boundary recheck: **27 passed**.
- Local AI tests: **13 passed**; no OCR model loading.
- Frontend Node tests: **5 passed**; TypeScript checks and production build passed.
- Full Playwright product regression: **239 passed** (4.7 minutes).
  The existing runner's 120-second whole-suite timeout was insufficient for 239 cases;
  a temporary external wrapper allowed 900 seconds. Per-test timeouts, assertions,
  fixtures, browser config, and the repository runner were unchanged.
- Real API lifespan/worker startup, login, material list and saved Gemma Map reads
  passed with the model HTTP transport offline and zero outgoing calls.
- Invalid model, exact revision mismatch, second model, context mismatch, rehashed
  stored provenance, and retryable 503 behavior are covered by passing tests.
- OCR block comparison is byte-for-byte identical; Assessment prompt/checker text
  equality and material prompt suffix-only equality were verified.

Three existing browser navigation expectations needed alignment with the accepted UI:
the renamed study-entry button, explicit access to both library revisions, and opening
saved answer history after restoring the current learning state. No product UI or
learning behavior changed, and the ownership, persistence, feedback and idempotency
assertions remain in place.

Static validation does not claim live preflight, material/Assessment smoke, or the
45-page quality gate. Those require a separately recorded runtime qualification.

The completed [runtime qualification](gemma-runtime-qualification.md) records the
real 45-page product run, browser/answer checks, historical Qwen comparison, remaining
limitations and the user-directed adoption criterion. The original 85% score is
reported separately and was not changed to a pass.

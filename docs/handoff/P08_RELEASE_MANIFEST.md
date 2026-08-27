# Phase 08 Release Manifest

`P08 EXECUTION PLAN FROZEN` 是本次 release qualification 的 scope 與 acceptance
authority。下列 revision 均來自 2026-08-27 的實際 Git、runtime 與 workstation；文件中的
舊 SHA 若不同，以本 manifest 為準。

## Release candidate

| Item | Frozen value |
|---|---|
| Production RC / exact `origin/dev` | `c65aeac51ac0bde7b18b5490c2a4201adb028802` |
| P07 integration | fast-forward `118d3db` → `ff9ddc6`; pushed to `origin/dev` |
| P08 P1 fix | `c65aeac` — kernel parent-death guard prevents orphaned local AI processes |
| P08 branch | `feature/p08-release-hardening-20260827` from exact RC |
| Production tree | `16268d34c794da48c69c8ab86d96c8b4938b03f0` |
| Frontend build | RC source; `index.html` SHA-256 `26427bb2…b2f5f` |
| Frontend JS | SHA-256 `eca0d2df…d0fc` |
| Frontend CSS | SHA-256 `991f3b52…14d7` |

P08 found one real P1: killing the backend during material processing could leave its separately
grouped vLLM process resident on the GPU. The focused `c65aeac` fix adds a stdlib-only Linux
parent-death guard, binds its code hashes into the material runtime identity and adds integration
coverage. It changes no model, parser, domain schema, dependency or public contract. Remaining P08
branch changes are aggregate release documents only.

## Public and persisted contracts

| Contract | Frozen identity |
|---|---|
| Canonical OpenAPI fixture | SHA-256 `0660aabb06a81e1e6992602fc5692f713ed1dec5bb279f97ed889606c083367c` |
| P06/P07 public fixture | SHA-256 `360c634b843764a85e50d31ec569aefea241a0d55feea8522d6b2869b098aa58` |
| Knowledge Map | `knowledge-map-view/v6` |
| Relation taxonomy | exactly `prerequisite`, `contains`, `related` |
| StudySession / context | `study-session/v1`, `study-context/v1` |
| Assessment public / private | `single-choice-assessment-public/v1`; private server answer `single-choice-assessment-answer/v1` |
| Feedback | `answer-feedback/v1` |
| Learning State / Weakness | `learning-state/v1`, `weakness/v1` |
| Adaptive / Suggestion | `adaptive-plan/v1`, `adaptive-response/v1`, `learning-suggestion/v1` |
| Migration head | `0013_add_assessment_request_idempotency.sql`; fresh ledger `1..13` |

Browser responses remain closed public projections. The client submits only assessment, question,
selected-option and idempotency identity; correctness is resolved from the server-private answer.

## Local runtime

| Item | Frozen identity |
|---|---|
| Material runtime lock | `studydy-local-ai-runtime-lock/v4`; SHA-256 `69a0c903…cd52` |
| Assessment runtime lock | `studydy-assessment-runtime-lock/v1`; SHA-256 `31785eee…eb6` |
| OCR | Unlimited-OCR revision `07dea832e22aefee32ad281d4b80551282e1c168` |
| Concept / proposal model | `Qwen/Qwen3-14B-AWQ`, content revision `5a690dbf…7619` |
| Relation / Assessment verifier | `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`, revision `8adb042d…64c` |
| Assessment policy | `assessment-generation-policy/v2`; reject without unsafe fallback |
| vLLM / PyTorch / CUDA runtime | vLLM `0.26.0+cu129`; PyTorch `2.11.0+cu129`; CUDA `12.9` |
| Installed runtime verification | 29/29 expected files verified |

The runtime uses an owned loopback Concept API with no credentials. Runtime/model/cache roots and
raw model output remain local and are intentionally absent from Git evidence.

## Canonical workstation

| Item | Measured value |
|---|---|
| Host | Windows 11 Pro, build `26200.9168` |
| WSL / distro | WSL `2.7.11.0`; Ubuntu `26.04 LTS`; kernel `6.18.33.2` |
| CPU / WSL memory | Intel Core i7-12700K, 10 cores / 20 threads; 15.49 GiB RAM + 4 GiB swap |
| GPU | NVIDIA GeForce RTX 5060 Ti, 16,311 MiB, compute capability 12.0 |
| NVIDIA driver | `610.88` |
| Python | app venv `3.13.15`; local model runtimes `3.12.x` |
| Node / npm | Node `24.19.0`; npm `11.17.0` |
| Docker | Engine `29.7.2`, overlayfs/Linux |
| PostgreSQL | pinned `postgres:18.4-bookworm` digest `sha256:882236b…fa382` |
| Frontend | React `19.2.8`; TypeScript `7.0.2`; Vite `8.2.1`; Playwright `1.62.1` |

## Baseline evidence

- Backend/local-AI deterministic regression: 326 passed.
- Recovery-focused regression: 9 passed.
- Frontend unit: 4 passed; typecheck and production build passed.
- Full-stack Playwright with fresh PostgreSQL: 14 passed.
- Every disposable qualification database applied fresh migrations 1–13.
- Installed runtime, real PostgreSQL, real local AI, release frontend and Chromium were exercised on
  the exact RC.

Private source details, screenshots, detailed logs and performance samples are retained only in the
qualifying workstation's ignored local evidence bundle.

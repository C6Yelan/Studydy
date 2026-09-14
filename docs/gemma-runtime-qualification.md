# Gemma 4 product qualification

**Accepted with limitations under the user-directed comparison with the historical
Qwen baseline.** The real product pipeline and learning/answer flow completed.
The original 85% material review gate remains **failed**; it was not lowered or
reported as passed. Adoption follows the updated criterion: improvement over the
historical Qwen result plus a working product flow, with remaining errors recorded.

## Candidate and runtime

| Item | Verified value |
|---|---|
| Original product SHA | `6180f13c869d7b7419d1d23538141cd729b24700` |
| Static candidate tested | `8a605206ccefca70f204872478410744b4a5ac24` |
| Branch | `be/feature-gemma4-cutover` |
| Semantic model | `google/gemma-4-31B-it-qat-w4a16-ct` |
| Product-locked revision | `52f3f65bc7a02d555763bc923bd1d9094898219d` |
| Runtime-lock canonical SHA-256 | `3b3910d228b6fbc1cbf893462b387259f4568b9a0c5f52703671dbf0111020fb` |
| vLLM / image | `0.28.0` / `vllm/vllm-openai:v0.28.0` |
| Python | observed `3.12.3`; qualified lock pins minor `3.12` |
| torch / CUDA / transformers | `2.13.0+cu130` / `13.0` / `5.15.1` |
| GPU | one NVIDIA A40, 46068 MiB |
| Service | server bind `0.0.0.0:18000`; backend target `http://127.0.0.1:18000` |
| Context / concurrency | 32768 / one sequence |
| OCR | unchanged Unlimited-OCR, revision `07dea832e22aefee32ad281d4b80551282e1c168` |

The existing Pod launch configuration was left unchanged at the user's request.
It omits `--revision`; its sole cached snapshot and resolved cache reference match
the product-locked revision above. This report does not claim that the existing
launch command pins that argument. No Pod restart, model replacement, or second
semantic service was performed during qualification.

The qualified launch command, including the revision for a future controlled launch,
is in [the runbook](runbook/A40_FINAL_WORKSTATION.md). It retains GPU utilization
0.90, `--reasoning-parser gemma4`, default `enable_thinking=true`, and xgrammar with
`disable_any_whitespace=true`. Bearer authentication remains supplied through
`VLLM_API_KEY`.

Preflight verified `/version`, exactly one `/v1/models` entry, 32768 context, and
`/tokenize` (17 tokens for the readiness input). Unauthenticated model discovery
returned 401; the existing bearer returned 200. The production client also passed
its loopback health/version/model/tokenizer checks before and after the material run.
The same engine remained resident. Observed error, abort and preemption counters
were zero; retained telemetry samples peaked at 41667 MiB. This is a sampled peak,
not continuous GPU profiling.

## Real product run

The approved 45-page source SHA-256 is
`07b1c1c1352934f75cc5182aa15db8a702138861f7557f470f9200ac33b06d13`.
A fresh test-owned material was uploaded through the real product API. The existing
worker performed native extraction/Unlimited-OCR, Gemma semantics and publication
into the existing persistent product storage. Other users' data was not rewritten.

| Measurement | Result |
|---|---:|
| Started / completed (UTC) | 2026-09-14 14:39:08 / 15:27:33 |
| Wall time | 2904.58 s (48 min 25 s) |
| Evidence / semantic time | 310.559 s / 2591.544 s |
| Pages / excluded pages | 45 / 0 |
| Actual OCR / semantic calls | 33 / 22 |
| Evidence rows / sections | 411 / 64 |
| Concepts / Claims / Relations | 67 / 94 / 59 |
| Literal repair events | 62 |
| Rejected Claims / Relations | 0 / 22 |
| Processing / quality / decision | `partial` / `needs_review` / `review` |
| Reason codes | `LITERALS_RESTORED_FROM_SOURCE`, `RELATIONS_REJECTED` |

Published revision:
`knowledge-structure:sha256:deb6a9a66f81ffbe97d3a99bcf11c6835357f33d2d2d05f581c174531741e203`.

All 411 freshly acquired Evidence rows exactly match the historical Qwen and
competition-final Gemma references. This run actually made 33 OCR calls; competition
final replayed cached Evidence with zero OCR calls. The schema, semantic policy and
qualified language suffix remain the same. No semantic prompt tuning or automatic
Relation reversal was introduced.

## Quality comparison

The same source-grounded Relation rubric is used: S = supported, U = confirmed
unsupported, H = needs human review, I = insufficient Evidence. These are assistant
Evidence audits, not human gold annotations. Confidence values are not truth labels.

| Measurement | Historical Qwen | Competition-final Gemma | Current product Gemma |
|---|---:|---:|---:|
| Concepts / Claims | 56 / 82 | 41 / 71 | 67 / 94 |
| Relations | 68 | 31 | 59 |
| S / U / H / I | 29 / 31 / 4 / 4 | 19 / 7 / 4 / 1 | 29 / 25 / 3 / 2 |
| S / all | 42.6% | 61.3% | 49.2% |
| S / (S + U) | 48.3% | 73.1% | 53.7% |
| Unsupported prerequisite flags | 11 | 2 | 1 |
| Wrong prerequisite direction flags | 4 | 1 | 0 |
| Wrong part_of flags | 15 | 3 | 1 |
| Wrong application direction flags | 4 | 1 | 3 |
| Wrong example direction flags | 1 | 1 | 20 |

The current run retains 29 supported edges while reducing confirmed unsupported
edges from 31 to 25 compared with Qwen. Prerequisite and composition errors are
substantially fewer. Example direction is worse and remains a clear limitation.
The current run does not reproduce competition final's higher Relation precision.

Major teaching regions and worked examples remain represented, including string
copying that the Qwen reference omitted. Counts alone are not a coverage score.
All 94 Claim meanings are supported by source text/context; the 2D parameter Claim
stays scoped to 2D rather than making the prior unsupported multidimensional
generalization. Its selected annotation refs could still be more complete.

All 61 source-projected Claims match their source-span joins up to whitespace.
No new published technical-literal corruption was observed. Existing source/OCR
defects are preserved and are not attributed to Gemma. Generated language passed:
63 labels contain Traditional Chinese, four preserve source technical sort names,
aliases are empty, and all 59 Relation reasons contain Chinese. Code and identifiers
were not translated.

Gemma's semantic time was 2591.544 s versus Qwen's 1321.907 s, approximately **1.96×
slower**. These are observed product configurations on one source, not a controlled
claim of universal model superiority.

## Smokes and product behavior

- Small material smoke: one live call, 63.78 s, four Concepts and one contrast
  Relation. JSON/schema/Evidence checks passed. A raw terminator mistake was
  contained by the existing source-literal repair; published code stayed intact.
- Thin synthetic Assessment input: generation exhausted the qualified 4096-token
  budget (4095 reasoning tokens), producing no usable question. The failure is
  retained; no prompt or token-budget change was made.
- Exact competition-successful Assessment input: unchanged prompt/config and
  identical request, three candidates, four options, checker and feedback document
  passed in 90.29 s using two calls.
- Fresh product material Assessment: real API generation/checker passed in
  123.64 s. The public response contained no private answer fields.
- Isolated Playwright contexts exercised real login, material discovery, Map,
  Concept navigation, actual source link/PDF hash, reload and fresh-login mobile
  reopen. Map reads made no processing or Assessment POSTs.
- Real browser answer submission returned correct source-backed feedback. Exact
  idempotent replay returned the same result, the event watermark remained one,
  and no Concept was falsely marked mastered. Guidance, reload and a new mobile
  login preserved the saved answer and feedback.

No product UI, learning-flow, OCR contract, database schema, or semantic policy was
changed for these checks. Browser harness navigation was corrected to wait for
authenticated UI before opening private routes.

## Acceptance and verification

The original scorer was executed with its unchanged 85% threshold. Reviewing each
canonical Claim and published Relation as one unit gives **123 / 153 = 80.4%**.
Its results are material=false, assessment=true, runtime=true, closed_loop=true,
overall=false. The approved corpus identity replaced the former different PDF hash;
this did not change the numeric gate or source/revision checks.

The user-directed adoption criterion instead compares the actual product result
with historical Qwen and requires a working product flow. That criterion passes
with the limitations above. The original failed score remains recorded separately.

Static verification: backend 239 passed, local AI 13 passed, frontend Node 5 passed,
full Playwright product regression 239 passed, TypeScript/build passed. The final
tokenizer/boundary recheck passed 27 tests. Qualification metadata/source-identity
updates passed the relevant 50-test group and seven scorer tests.

Runtime-contract/generation/prompt before-and-after details and the exact language
suffix are in [the cutover audit](gemma-cutover.md). OCR's lock block was compared
byte-for-byte and is unchanged; both Assessment prompts are unchanged.

Additional read-only references used for this report:

- Competition final: `knowledge-structure.json`, `runtime-metrics.json`,
  `03_CONCEPT_COMPARISON_QWEN.md`, `05_RELATION_COMPARISON_QWEN.md`,
  `audit_relations.py`, `audit_literals.py`, `assessment/request-1.json`.
- Historical reference: `competition/review/qwen-existing-structure.json`, with
  its Qwen identity, source hash and all 411 Evidence rows verified before comparison.
- Existing product qualification scorer and browser/closed-loop contracts.

Only aggregate, non-private qualification information belongs in this report.
PDFs, source/output panels, screenshots, account credentials and detailed review
artifacts remain outside Git. No secrets, private artifacts, model compatibility
layer, fallback model or merge is included.

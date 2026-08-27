# Phase 08 Real Full-Flow Qualification

This report is aggregate-only. It intentionally excludes private filenames, source text, raw model
output, private answers, learner identifiers, DSNs and runtime paths.

All R1/R2/R3 results below were rerun after blocker closure on exact production RC
`c65aeac51ac0bde7b18b5490c2a4201adb028802`.

## Representative material matrix

| Profile | Pages | Production result | Map / study result | Assessment result | Classification |
|---|---:|---|---|---|---|
| R1 normal digital technical material | 15 | `partial`; 1 excluded page; 0 OCR / 15 Concept calls | 7 Concepts; canonical path 7; StudySession usable | real wrong → remediation/review → distinct-item correct reassessment | `FULL_LOOP_PASS` |
| R2 formula/drawing-heavy real excerpt | 7 | `succeeded`; 0 excluded; 0 OCR / 7 Concept calls | 4 Concepts; canonical path 4 | safe single-choice item, HTTP 201 | `PARSING_HEAVY_PASS` |
| R3 image-heavy/fallback real excerpt | 6 | `partial`; 2 excluded; 0 OCR / 6 Concept calls | remaining safe content produced 4 Concepts and path 4 | no safe item, HTTP 404 fail closed | `EXPECTED_SAFE_PARTIAL` |

The bounded R2/R3 excerpts contain only pages copied from real private PDFs. They are not synthetic
fixtures, fake providers or manually authored model inputs. No parser, OCR candidate, model, prompt,
Relation Gate or Assessment Gate changed for qualification.

Across R1–R3:

- unresolved system failure: 0;
- unsafe promotion: 0;
- unsupported Relation promotion: 0;
- public/private response leak: 0;
- stuck or orphan run: 0.

R3 is an accepted fallback case: safe content reaches Map and StudySession while Assessment refuses
to invent or replay an unsafe item.

## Real learner adaptation closed loop

R1 was driven from the release frontend in Chromium through upload, processing, Map, Concept,
StudySession, Assessment, server Feedback, Learning State, Weakness, Adaptive Plan and reassessment.

| Check | Result |
|---|---|
| First answer | intentionally wrong; server returned incorrect Feedback |
| First watermark | 1 |
| Backend next step | `review` |
| Reassessment diversity | different `question_id`; same Claim coverage |
| Second answer | server-private correct option; server returned correct Feedback |
| Second watermark | 2 |
| State / Weakness / Adaptive revisions | all changed |
| Canonical Map | byte-canonical public hash unchanged |
| `initial_learning_path` | unchanged |
| Browser scoring | none; only server Feedback accepted |

The private answer was read only by the local qualification controller to choose a deterministic
wrong then correct browser click. It was never returned to, stored by or evaluated in the browser.
The observed public Assessment document had only the frozen public fields.

## StudySession isolation

Session B was created for the same real material after Session A accumulated two AnswerEvents.

- distinct StudySession identity;
- event watermark 0;
- inherited valid attempts 0;
- inherited mastery 0;
- inherited `observed_weak` / `needs_review` findings 0;
- inherited prerequisite gaps 0;
- wrong-session Assessment read rejected;
- seven fresh `not_enough_data` findings were allowed because they describe B's own zero-data state;
- B's conservative next action was `collect_more_data`.

This proves no cross-StudySession learner profile or mastery aggregation was introduced.

## Knowledge and Relation semantics

- Production taxonomy remained exactly `prerequisite`, `contains`, `related`.
- R1/R2/R3 published no Relation rather than lowering precision.
- `contains` and `related` enter neither immediate-prerequisite gap selection nor hard prerequisite
  routing; the full regression and public fixture gates passed.
- Learner events changed only StudySession-scoped artifacts. Agent 4 did not write a new Knowledge
  Map revision or alter the canonical initial path.

No eligible non-cycle real prerequisite was found in the representative release set:

`REAL_ELIGIBLE_PREREQUISITE_SAMPLE_NOT_FOUND`

The frozen canonical non-private fixture therefore supplies the prerequisite remediation contract
demonstration. It proves immediate published prerequisite routing, deferred-target return and Map/path
immutability without claiming that any P08 real PDF produced a prerequisite.

## Local performance observations

| Profile | Material | Assessment | Peak VRAM | Maximum observed WSL used RAM | Local cache delta |
|---|---:|---:|---:|---:|---:|
| R1 | 346.687s | cold 133.214s; warm 19.397s; final warm 18.874s / 18.934s | 15,116 MiB | 13,508.0 MiB | 140,013 bytes |
| R2 | 333.104s | cold safe item 137.385s | 15,128 MiB | 13,205.3 MiB | 140,083 bytes |
| R3 | 236.207s | cold fail-closed 134.866s | 15,054 MiB | 13,044.5 MiB | 69,404 bytes |

No repeated OOM, client timeout, unbounded cache growth or browser freeze occurred. Cold latency is
visible but within the measured workstation Demo window; warm reuse matches the P06 baseline.

# P06 Real-Material Qualification

This report contains aggregate-only evidence. Source filenames, source text, raw
model output, private answers, and private Golden annotations are intentionally
not recorded.

## Scope and stop condition

Qualification selected nine materially different inputs from both approved
sources. It covered original PDFs, locally converted presentation material,
two real textbook chapter excerpts kept only in `/tmp`, short and long inputs,
low and high Concept density, technical and general text, and formula/table
layout. Sampling stopped when additional inputs repeated the same published-Map
and safe-item outcomes without exposing a new Agent 4 failure surface.

| Case | Coverage added | Material result | Map / Assessment result | Classification |
| --- | --- | --- | --- | --- |
| R01 | Short technical slides; replay baseline | partial, 7 Concepts | no Relations; 2 distinct safe items | FULL_LOOP_PASS |
| R02 | Mounted-source data-structure PDF | partial, 11 Concepts | no Relations; 1 item, then no-new-safe-item | EXPECTED_FAIL_CLOSED |
| R03 | Long system-analysis material | succeeded, 35 Concepts | no Relations; 1 item, then no-new-safe-item | EXPECTED_FAIL_CLOSED |
| R04 | Mounted-source presentation converted to PDF | partial, 24 Concepts | no Relations; 1 item, then no-new-safe-item | EXPECTED_FAIL_CLOSED |
| R05 | Formula/table sampling material | partial, 19 Concepts | no Relations; 2 distinct safe items | FULL_LOOP_PASS |
| R06 | Tree/data-structure presentation | partial, 15 Concepts | 1 published `related`; 2 distinct safe items | FULL_LOOP_PASS |
| R07 | High-density real textbook excerpt A | partial, 24 Concepts | 1 published `related`; 2 distinct safe items | FULL_LOOP_PASS |
| R08 | High-density real textbook excerpt B | partial, 26 Concepts | no Relations; no safe candidate | EXPECTED_FAIL_CLOSED |
| R09 | General-text material | producer partial | formal resolution rejected missing Claim | EXPECTED_FAIL_CLOSED |

There were no `SYSTEM_FAILURE` or `UNSAFE_PROMOTION` cases after classification.
R09 was isolated to deterministic Agent 3 formal-resolution validation:
model startup 49.019s, inference 104.499s, then immediate
`RESOLUTION_CLAIM_MISSING`. The run correctly failed closed; changing Agent 3
resolution semantics or adding material-specific behavior is outside P06.

## Closed-loop and stability evidence

- Multiple real cases reached deterministic scoring, AnswerEvent persistence,
  Learning State, `needs_review`, and Adaptive `review`.
- R01 was replayed with two wrong distinct items and reached
  `observed_weak -> practice` at event watermark 2.
- R01 exact Assessment replay completed in 0.050s with the identical public
  document. Answer replay was identical, Adaptive Plan revision was stable,
  and a new StudySession remained at watermark 0.
- R02/R03/R04 correctly rejected exhausted item supply with
  `ASSESSMENT_NO_NEW_SAFE_ITEM`; R08 rejected with
  `ASSESSMENT_NO_SAFE_CANDIDATE`. No case reused an old item or lowered a Gate.
- Across all promoted real items: unsafe promotion 0, answer leakage 0,
  client-controlled scoring 0, state corruption 0, and cross-session
  contamination 0.

## Assessment runtime reuse

Before the targeted fix, consecutive real Assessments were both cold:
142.533s and 147.431s. After the fix, a representative pair measured:

- cold first request: 130.235s;
- warm second request: 18.620s;
- Qwen startup: 30.715s;
- verifier startup: 5.078s;
- proposal inference: 82.381s cold, 6.703s warm;
- verification: 0.674s cold, 0.057s warm;
- shutdown: 1.896s;
- repair: not triggered in that case.

Across the wider set, successful warm requests were 18.620–33.671s while
preserving distinct question identity. The reusable lifecycle owns the existing
material-analysis OS lock, is lazy, releases after bounded idle time or app
shutdown, and discards both model processes after any failed generation. It
does not change P06-03 prompts, thresholds, repair, grounding, selection,
public/private fields, or runtime policy identity.

## Coverage gap and handoff

None of the selected real materials published a non-cycle `prerequisite`.
Two cases published `related`; diagnostics across high-density excerpts showed
many candidate pairs but no structural prerequisite proposal. Therefore the
real-material prerequisite-remediation route remains an upstream sample gap,
not an Agent 4 fallback opportunity. Agent 4 prerequisite semantics,
cycle/contains/related negatives, deferred-target return, and reassessment are
covered by the automated canonical-Map contract and public API closed-loop
Golden. P07 must not synthesize prerequisite edges or reinterpret `related`.

Public P07 examples are frozen in
`backend/tests/runtime/fixtures/phase06-public-fixtures-v1.json`; canonical
OpenAPI is frozen in `backend/tests/runtime/fixtures/openapi-v2.json`.

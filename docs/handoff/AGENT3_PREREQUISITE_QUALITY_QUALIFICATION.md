# Agent 3 Prerequisite Quality Qualification

This report records aggregate-only qualification evidence. Private source names,
source text, raw model output, and runtime artifacts are intentionally excluded.

## Production scope

- Expanded direction-safe English and Chinese explicit prerequisite wording.
- Added fail-closed negation and non-learning dependency protection.
- Ranked explicit relation evidence ahead of other candidate signals under the
  existing 128-pair ceiling.
- Kept page order, adjacency, same-page, and same-group metadata as candidate
  signals only; none can publish a prerequisite.
- Kept the existing Evidence Gate, frozen relation verifier, endpoint rules,
  cycle handling, relation types, and Agent 4 consumer unchanged.
- Did not add inferred pedagogical prerequisite production. The current verifier
  validates an already grounded structural proposal; it is not a safe proposal
  generator. Adding a separate grounded proposal boundary requires its own task.

## Deterministic benchmark

The committed `prerequisite-quality-benchmark/v1` fixture contains 24 explicit
positive cases and 23 negative cases. Positives cover English and Chinese
direction variants. Negatives cover adjacent topics, negation, runtime,
implementation, installation, memory, parameter, input, performance, quality,
coursework, and project dependencies. The negative set includes five Chinese
negation forms covering `不需要`, `不依賴`, `不是`, `並非`, and `沒有依賴`.

| Measure | Result |
| --- | ---: |
| Positive cases | 24 |
| Detected positives | 24 |
| Chinese positive cases | 10 |
| Detected Chinese positives | 10 |
| False negatives | 0 |
| Negative cases | 23 |
| False positives | 0 |
| Direction errors | 0 |
| Endpoint errors | 0 |
| Evidence ownership errors | 0 |
| Verifier rejects in benchmark corpus | 0 |
| Cycle rejects in benchmark corpus | 0 |
| Unsafe publications | 0 |

Separate contract tests prove that verifier rejection publishes no edge,
opposite-direction evidence fails closed, explicit pairs survive the pair
ceiling, adjacent-only pairs do not publish, and existing `contains`, `related`,
cycle, Evidence ownership, and canonical identity behavior remains valid.

## Real-material qualification

Both approved private source locations were available, including the mounted
source. Five representative technical cases were selected across data-structure
progression, programming foundations, database normalization, and statistical
material. Existing canonical artifacts contained 11–35 Formal Concepts and
11–64 selected relation candidates per case.

Targeted replay through the current pair selector and structural Evidence Gate
found no explicit prerequisite proposal. Targeted source wording checks also
found no eligible explicit wording. Additional inputs were not scanned after
the selected cases repeated the same safe outcome without exposing a new
failure surface.

| Classification | Count |
| --- | ---: |
| `REAL_PREREQUISITE_PASS` | 0 |
| `EXPECTED_NO_PREREQUISITE` | 5 |
| `EXPECTED_FAIL_CLOSED` | 0 |
| `SYSTEM_FAILURE` | 0 |
| `UNSAFE_PREREQUISITE` | 0 |

Result: `REAL_ELIGIBLE_PREREQUISITE_SAMPLE_NOT_FOUND`. No real non-cycle
prerequisite was published, so no real-material Agent 4 remediation was claimed.
The canonical Agent 3-to-Agent 4 prerequisite contract and complete remediation
flow remain covered by deterministic integration and public closed-loop tests.

## Regression evidence

- Agent 3 generation and Relation targeted regression: 78 passed.
- Agent 4 prerequisite/adaptive targeted regression: 10 passed.
- Relation verifier protocol/process: 9 passed.
- Full backend and local-AI regression: 324 passed.
- Installed local runtime verification: 29 of 29 files verified.
- Full-stack material review: 5 passed with `MATERIAL_REVIEW_E2E_PASS`.
- Fresh migration smoke: 13 of 13 migrations applied by the full-stack harness.
- Storage and migration production code: unchanged.

No prerequisite precision, direction, provenance, verifier, `contains`,
`related`, cycle, or Agent 4 consumer regression was observed.

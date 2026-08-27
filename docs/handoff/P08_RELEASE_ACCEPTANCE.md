# Phase 08 Release Acceptance

All real profiles and final automated gates were rerun on exact production RC
`c65aeac51ac0bde7b18b5490c2a4201adb028802` after the P1 process-orphan fix entered `origin/dev`.

## Acceptance matrix

| Gate | Result | Blocking |
|---|---|---|
| RC freeze | PASS — exact production RC `c65aeac51ac0bde7b18b5490c2a4201adb028802` | no |
| Real E2E R1/R2/R3 | PASS / accepted safe partial | no |
| Knowledge / Evidence / canonical path | PASS | no |
| Assessment generation and private scoring | PASS | no |
| Real adaptation / distinct-item reassessment | PASS | no |
| StudySession A/B isolation | PASS | no |
| Recovery RCV-01…06 | PASS | no |
| Security / privacy | PASS | no |
| Performance regression | ACCEPTED | no |
| Demo Cold | PASS | no |
| Demo Warm | PASS | no |
| Demo Recovery | PASS | no |
| P0 | 0 | — |
| P1 | 0 | — |

## Reliability / recovery

| Scenario | Evidence | Result |
|---|---|---|
| RCV-01 backend restart during processing | real three-page job; restart 11.834s; old run `failed/RESTART_INTERRUPTED`, binding absent; retry `succeeded` in 127.800s; exactly one output and one Map; stuck running 0; host vLLM/guard PID 0 after cleanup | PASS |
| RCV-02 Assessment failure / timeout | real subprocess boundary plus focused invalid-startup/timeout and lifecycle-discard regression; no unsafe item/state write; next request starts clean | PASS |
| RCV-03 browser refresh | real Session A recovered at watermark 2 with identical current context/revisions | PASS |
| RCV-04 app session expiry | cookie recreated; old learner-owned StudySession remained server-bound and inaccessible to the new owner | PASS |
| RCV-05 duplicate / idempotent submission | exact replay returned the same AnswerEvent; conflicting replay rejected; watermark did not duplicate | PASS |
| RCV-06 stale / wrong StudySession | stale plan, wrong-session Assessment and cross-owner reads rejected | PASS |

Fresh migrations 1–13 passed in the baseline and every real disposable stack. Out-of-order,
cross-owner, invalid structured output, cycle, stale revision and relation-verifier rejection remain
covered by the 326-test regression.

## Security / privacy

- 184 tracked files scanned; restricted tracked files 0.
- Private key, API token, credential DSN and private absolute-path matches: 0.
- Private material, ignored evidence, model/cache and runtime outputs are not tracked.
- Production frontend source/build contains no private answer, correct option, private scoring,
  generation provenance, raw model or runtime-path field.
- R1 checked 324 public JSON responses; R2 checked 230; R3 checked 166. Private-field leak: 0.
- Learner identity is cookie/server-bound. Wrong session and cross-owner access fail closed.
- Formal model services are loopback only. No external model/provider was used.

## Performance baseline

- R1/R2/R3 material duration: 236.207–346.687s for 6–15 pages.
- Assessment cold: 133.214–137.385s.
- Assessment warm: 18.874–19.397s.
- Peak VRAM: 15,054–15,128 MiB on a 16,311 MiB GPU; repeated OOM: 0.
- Maximum observed WSL used RAM: 13.51 GiB; no monotonic growth across profiles.
- 30-Concept Map/narrow interaction regression: 1.143s.
- StudySession wrong/reassessment UI interaction regression: 1.045s.
- Local validated cache growth was below 0.15 MB per representative profile.

Cold latency remains an accepted V2 limitation. Warm reuse is working and there is no
release-blocking regression, client timeout, resource leak or browser freeze.

## Demo readiness

- Cold: fresh services, real R1 upload/partial Map, first real Assessment, wrong answer and state
  update; exact stack elapsed 561.269s.
- Warm: the same running stack reused the ready Qwen/verifier for distinct items and the final
  learner narrative at 18.874s / 18.934s per Assessment.
- Recovery: real running material job was interrupted by backend restart, safely failed, then a new
  idempotency run completed with no orphan or stuck state. The P1 fix is `c65aeac`.
- The canonical workstation procedure is frozen in
  [`V2_CANONICAL_WORKSTATION.md`](../runbook/V2_CANONICAL_WORKSTATION.md).

## Accepted limitations

- `REAL_ELIGIBLE_PREREQUISITE_SAMPLE_NOT_FOUND`; prerequisite remediation is demonstrated with the
  frozen non-private canonical fixture.
- R3 has no safe Assessment item and correctly fails closed after publishing a safe partial Map.
- Cold Assessment is about 134–137 seconds on this workstation.
- The representative real set published no Relation; precision was not reduced to manufacture one.
- Evidence viewer remains original PDF + exact page. Narrow layout is functional, not a separate
  full mobile visual system.
- A second human operator was not available during qualification. The runbook was instead replayed
  through repeated clean disposable PostgreSQL/backend/frontend/browser stacks; independent reviewer
  replay remains recommended but is not a release blocker under the frozen plan.

## V3 product-quality backlog

- Assessment cold-start reduction and additional warm residency telemetry.
- Broader parser/material profiles and richer B2 formula/table quality evaluation.
- Higher real-material prerequisite recall without changing Relation precision.
- Evidence crop viewer, graph layout, full mobile/accessibility and visual polish.
- Cross-session learner history, richer Assessment types and spaced repetition remain new V3
  product scope, not P08 repairs.
- Replace the currently working Starlette/httpx integration before its reported deprecation becomes
  a dependency-upgrade issue.

## Final decision

`P0 = 0`

`P1 = 0`

`STUDYDY V2 RELEASE ACCEPTED`

`P08 RELEASE HARDENING READY_FOR_REVIEW`

# V2 Product Usability Correction — Acceptance Handoff

## Candidate and scope

- Branch: `feature/v2-usability-correction-20260827`
- Base: `a3ad14d7c7061adb8767a5bf23c382bdb73eb787`
- Accepted product candidate: `c1b584fb69f2b8d86b584adeb9c17a53331b7f6c`
- Documentation candidate: this file’s eventual signed commit SHA, to be supplied by the final report after commit. The product SHA is not a claim that `dev` or `main` has been merged.

This is the single acceptance handoff for the full V2 usability correction: Knowledge Map topology, Relation recall, Claim granularity and grounding, Assessment supply, Weakness and Path projection, the Adaptive loop, and student copy. The final incremental commit at `c1b584fb…` is copy-boundary focused: student-facing adaptive, weakness, and assessment rationale text is readable Traditional Chinese while machine-readable action/reason codes, schemas, scoring, bindings, and canonical data remain unchanged. Semantic/model behavior was not rerun for that copy-only increment; the accepted semantic evidence below is inherited from the preceding exact product candidate where stated.

## Acceptance evidence

### Before/after qualification

The comparison is intentionally transparent because the sources and denominators differ; it is not an exact same-file statistical improvement claim.

| Evidence point | Before / audit | Final accepted result |
| --- | --- | --- |
| Knowledge Map audit | 20 Concepts, 0 Relations | 4/4 Maps usable; 15 Concepts, 62 Claims, 4 related Relations, 15 path steps |
| Sampled first/distinct assessment | First 1/4 and distinct 1/4 | First 8/8; distinct 7/8 |
| Intermediate matrix | 0/4 Maps | Final matrix 4/4 Maps |
| Raw semantic review | — | M1: 11/11; M3: 10/11 independently assessable, with one diagram-dependent but grounded Claim |

Coverage and grounding were preserved conservatively: all 3/3 eligible Relation materials had at least one evidence-backed edge; no unsupported prerequisite was fabricated. Canonical Map bytes, revision, and path were immutable before/after, including session-bound browser use.

The final real browser evidence reached mastery, showed the next learning step, and demonstrated a truthful no-safe outcome without writing an extra AnswerEvent. The copy candidate additionally passed producer and rendered-DOM checks for adaptive guidance, weakness guidance, and wrong-answer feedback.

### Verdicts

- **TECHNICAL — PASS:** backend 313/313, local_ai 23/23, frontend 30/30, typecheck/build, official Chromium 17/17, and focused security/ownership/scoring/immutability checks 61/61. The copy-targeted producer checks were 37/37.
- **SAFETY — PASS:** no confirmed semantic, privacy, security, ownership, stale-binding, or server-scoring failure remained.
- **REAL_COVERAGE — PASS:** the final real matrix is 4/4 usable Maps with conservative Claim and Relation grounding as documented above.
- **PRODUCT_DEMO — PASS:** assessment first 8/8 and distinct 7/8; real mastery-to-next-step behavior and truthful no-safe behavior completed in the browser.

The copy correction closed the student-copy boundary issue: public text no longer exposes canonical/formal implementation vocabulary, `StudySession`, `Relation`, `Evidence`, or `Single-choice` terminology. Stable machine values remain available to the runtime. The assessment runtime lock was updated and matched its required hash; no model, prompt, schema, API, scoring, or UI-structure change was introduced.

## Security and privacy boundaries

- Private answers were absent from public responses, the DOM, and logs; scoring remained server-side.
- Owner, session, Map, question, option, and stale bindings fail closed.
- Agent 4 does not create or mutate canonical Map or Path revisions.
- No secrets or private/raw/runtime artifacts were tracked or added.
- No blind-test data was used as design or acceptance authority.

## Honest remaining limitations

These are nonblocking quality or observability limits, not acceptance failures:

- One of eight distinct reassessments truthfully had no safe alternate after same-Claim and uncovered-Claim exhaustion; it wrote no event.
- Local-AI latency remains high: first assessment p95 about 186 seconds and distinct total p95 about 200 seconds.
- One grounded M3 Claim depends on its cited same-page diagram for full interpretation.
- No confirmed real prerequisite was available in the matrix, so none was invented.
- Internal proposal-versus-repair model-call splitting was not persisted; API request/outcome counts were available.

Potential V3 backlog is separate from this acceptance: graph physics, mobile expansion, multi-question assessment, and analytics are not part of this correction.

## Change and verification boundary

This handoff does not change production, tests, contracts, or runtime behavior. The candidate was verified with Markdown/path/link sanity, secret/private-term scans, and `git diff --check`; the final commit is documentation-only and must remain on this feature branch pending user review. No `dev`/`main` merge or force push is implied.

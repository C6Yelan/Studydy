# Phase 06 Current Handoff

> This is a short recovery checkpoint, not the product specification. GitHub / Git
> repository state always takes precedence over SHA or status recorded here.

- **Authoritative plan:** [`docs/planning/Phase_06.md`](../planning/Phase_06.md)
- **Current task:** P06-10 ready-to-start checkpoint.
- **Last completed task:** P06-09 — closed-loop integration, public Golden, and regression.
- **Base branch:** `dev`
- **Execution branch:** `feature/p06-agent4-closed-loop-20260827`
- **Base SHA:** `faf6cab741e3f87a590ea8f812bec5355c4dc041` (latest `dev` / `origin/dev` checkpoint at branch creation)
- **Milestone commits:** P06-04 `86c4303df3600d7c1e3e93ded556d9526dc61879`; P06-05 `364288ad6bb21628c3c668bdf39658e5018283f4`; P06-06 `afd90433c4d236637302243406f316789505dfc6`; P06-07 `350fc68f7aa468032863322628585265d7eff943`; P06-08 `d0b10749aafea53a91c15f8b01456df56a7668a7`; P06-09 is the signed Git tip created from this checkpoint.
- **Latest verification:** P06-09 public API closed-loop Golden 1 passed; combined closed-loop Golden, Assessment safety/protocol, and complete PostgreSQL/runtime regression 128 passed. P06-03 qualification remains representative 24/30 with critical 0, holdout critical 0, multiple-supported unsafe promotion 0, and public answer leakage 0.
- **P06-04 result:** client input is limited to StudySession / assessment / question / selected option / idempotency identity; scoring reads the validated private answer server-side; one immutable AnswerEvent per Assessment receives a StudySession-ordered event number; exact replay returns the same event, while idempotency conflict, duplicate, stale, cross-owner, cross-session, invalid option, and tampered scoring fail closed. Safe post-submit feedback omits answer key and generation provenance.
- **P06-05 result:** `learning-state/v1` is derived on read from the exact Map plus ordered trusted AnswerEvents under one StudySession lock. The content-addressed revision and event watermark are deterministic; mastery requires all current Claims covered with latest-correct results plus the frozen multi-Claim or single-Claim distinct-item evidence. Mastery band, confidence, Claim/Evidence coverage, repeated error, improvement trend, and `needs_more_data` stay separate. New StudySessions start with no evidence.
- **P06-06 result:** `weakness/v1` distinguishes repeated trusted errors (`observed_weak`), unstable/recent error (`needs_review`), and insufficient evidence (`not_enough_data`). Possible prerequisite gaps inspect only published non-cycle immediate `prerequisite` edges whose source Concept is not mastered; `contains`, `related`, cycle edges, and ancestry never enter the decision path.
- **P06-07 result:** `adaptive-plan/v1` binds the exact Map, inline path content hash, Learning State, Weakness, and event watermark to one primary step. Frozen priority routes immediate prerequisite, credible current weakness, deferred-target return, canonical path, low-data fallback, then completion. Applying an exact plan revision changes only server-validated StudySession current/deferred targets; stale plans fail closed. `learning-suggestion/v1` is a projection of that step and optional promoted-resource route, not a second decision engine.
- **P06-08 result:** the existing FastAPI `/v1` app now publishes StudySession lifecycle/context, public single-choice Assessment create/read, idempotent submission with safe feedback, Learning State, Weakness, Adaptive Plan + Suggestion, and exact-plan apply. Strict request/response models, cookie ownership, Origin, request idempotency, fixed safe errors, and canonical OpenAPI are wired without a parallel app or DB shortcut. Assessment generation runs in the existing synchronous lifecycle via a request threadpool; no worker/queue/runtime architecture was added.
- **P06-09 result:** the committed public Golden drives two distinct wrong target items, immediate-prerequisite remediation, two distinct correct prerequisite items, deferred-target return, and two new correct target reassessments through only `/v1` routes. Event watermark advances 0→2→4→6, weakness/state/plan revisions update, the target becomes mastered, the next canonical Concept is selected, stale plan/cross-session use fail closed, and a new StudySession remains empty.
- **Current blockers:** none. The known ~135s cold Assessment lifecycle remains a P06-08 escalation boundary, not a downstream blocker.
- **Next action:** perform P06-10 Phase-only cleanup, freeze public fixtures/contracts, run fresh migration/full backend/full-stack/local-runtime verification, and prepare the P07 handoff.

## Browser escalation / reviewer policy

- Use Browser + ChatGPT planner/reviewer for frozen-contract conflicts, a required P06-01～03 contract change, Agent 3 / Knowledge Map semantics changes, new model architecture, Blocker / High correctness-security-ownership-data-loss issues, or a P06-08 latency decision requiring a resident worker, queue, or model service.
- Medium / Low findings do not block a completed task; record them for P06-10 or post-freeze cleanup.
- For P06-04～10, each task must independently test, create a signed commit, push, and checkpoint. Never merge `dev` unattended.

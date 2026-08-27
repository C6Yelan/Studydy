# Phase 07 Current Handoff

> This is a recovery checkpoint, not the product specification. Git truth and
> the frozen `/v1` public contract take precedence over recorded SHAs.

- **Authoritative plan:** P07 Frontend Design Alignment Frozen Execution Plan.
- **Status:** P07-E release candidate complete; final Git audit is next.
- **Base SHA:** `118d3db50f7974c7197d84bdf6cfa7e3c8bb41ce`.
- **Base branch:** `dev` / `origin/dev`.
- **Execution branch:** `feature/p07-frontend-design-alignment-20260827`.
- **Milestone commits:** P07-00 `5d225230d3f71528538d7074e663af4b7083c218`;
  P07-A `dbf3f38227588965794e90748bf46a341832938f`; P07-B is the
  signed commit `ffa4a86836f6b2cb82c344412e00d36959fe4824`; P07-C is the
  signed commit `38284d4499b1a969a9d2b28d3f3a2048c8c2cc8f`; P07-D is the
  signed commit `5a5971fedb31b75f5f6aead8ea73874cc772e9d3`; P07-E is the
  signed commit containing this checkpoint.
- **Current candidate:** P07-E working tree ready for its signed release-candidate commit.
- **Prerequisite integration:** the approved Agent 3 prerequisite-quality
  commits `6921219`, `d4c50c6`, and `118d3db` were explicitly authorized,
  fast-forwarded, and pushed to `dev` before this P07 branch was created.
- **Latest tests:** frontend test command pass (4 test files); typecheck pass;
  build pass; P06 public
  fixtures 1/1 at baseline; existing full-stack Playwright 5/5 with
  `MATERIAL_REVIEW_E2E_PASS` after P07-A; P07-B full-stack Playwright 6/6
  with `MATERIAL_REVIEW_E2E_PASS`; P07-C full-stack Playwright 9/9 with
  `MATERIAL_REVIEW_E2E_PASS`; P07-D full-stack Playwright 11/11 with
  `MATERIAL_REVIEW_E2E_PASS`; P07-E full-stack Playwright 14/14 with
  `MATERIAL_REVIEW_E2E_PASS`; frozen backend API runtime/public fixtures 16/16.
- **Design ingestion:** General UI guideline, correct/wrong examples, blind-test
  results, final selected PNGs, visual reference, runtime asset manifest;
  Knowledge Map guideline/examples/blind-test sources; Mascot guideline,
  examples, and blind-test results; data-team prototype, frontend interaction
  spec, handoff prompt, and test checklist were inspected. The prototype was
  walked locally through upload-ready, processing, done, Focus, and Detail
  states.
- **Frozen alignment:** [`P07_FRONTEND_DESIGN_ALIGNMENT.md`](P07_FRONTEND_DESIGN_ALIGNMENT.md).
- **Contract source:** frozen OpenAPI `openapi-v2.json` and
  `phase06-public-fixtures-v1.json` on the base SHA.
- **Blockers:** none.
- **Known deviations:** original PDF + exact page is the Evidence viewer
  fallback; no account/settings/search capability; no Map-list API; no dedicated
  Assessment/Adaptive mockup; narrow responsive acceptance is functional rather
  than full mobile polish.
- **P07-A result:** centralized General UI semantic tokens; focused and
  workspace AppShell variants; real dynamic navigation only; thin inline icon
  family; shared loading/empty/failure/success/insufficient state surface;
  MaterialFlow split into upload/run responsibilities; approved runtime PNGs
  copied byte-for-byte from the official asset pack; upload, processing, done,
  failure, and narrow viewport visually inspected from local-only screenshots.
- **P07-B result:** data-driven Overview, canonical `教材建議學習順序`,
  Focus, and pre-session content Review views; Concept/Claim/Evidence/resource
  Detail; Relation Detail with only `prerequisite`, `contains`, and `related`;
  directional markers only for directional types; symmetric dashed `related`;
  no diagnostics in the student layer; safe HTTP(S) resource boundary; readable
  empty, partial, isolated-node, and responsive fallbacks. AppShell, Overview,
  Focus, Relation Detail, and Initial Path were visually inspected from
  local-only public-fixture screenshots.
- **P07-C result:** canonical StudySession route and strict refresh recovery;
  Map/Concept/Initial Path start actions; route/material/Map/session binding
  checks; current Concept/Claim/Evidence view; public single-choice parser with
  exactly four options; no private-answer fields in the client surface;
  idempotent assessment/submission retry identity; pending, no-safe-item,
  stale/conflict, public Feedback, new-item reassessment, and completed-session
  states. Current Concept, Assessment, Feedback, and completed session were
  visually inspected from local-only public-fixture screenshots.
- **P07-D result:** closed public parsers for Learning State, Weakness, and
  Adaptive Response; same-session/Map/state-revision/event-watermark binding;
  Map-context and published-prerequisite validation; separate state, mastery
  band, confidence, Claim/Evidence coverage, data sufficiency, and trend UI;
  distinct weakness categories; single primary `目前為你調整` card; exact-plan
  apply for every actionable backend action; safe resource routing; prerequisite
  current/deferred remediation and return copy; canonical path immutability;
  visible new-StudySession reset. Learning State/Weakness, Adaptive Next Step,
  and prerequisite remediation were visually inspected from local-only public
  fixtures.
- **P07-E result:** state matrix covers loading, empty, success, partial,
  insufficient, stale/conflict, retryable/fatal failure, expired app session,
  no/active/completed/new StudySession, no-safe-item, and empty/partial/30-Concept
  Map; session refresh is single-flight; named landmarks, keyboard Map tabs,
  associated input errors, native radio semantics, visible focus, reduced motion,
  and not-color-only Relations are in place; desktop and 390 px assessment/map
  overflow checks pass; 18/18 local-only acceptance screenshots are complete.
- **Acceptance matrix:** [`P07_DESIGN_ACCEPTANCE.md`](P07_DESIGN_ACCEPTANCE.md).
- **Blockers:** none.
- **Known limitations:** original PDF + exact page viewer fallback; no separate
  approved mobile visual system; static approved mascot assets; no unsupported
  account/settings/global-search capability; real-material prerequisite sample
  coverage remains upstream while the public fixture flow is automated.
- **Next action:** create and push the signed P07-E release-candidate commit,
  verify remote feature tip equals local candidate and `origin/dev` remains at
  the approved base, then record final candidate Git truth and mark
  `READY_FOR_REVIEW`.

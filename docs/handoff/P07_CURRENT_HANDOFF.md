# Phase 07 Current Handoff

> This is a recovery checkpoint, not the product specification. Git truth and
> the frozen `/v1` public contract take precedence over recorded SHAs.

- **Authoritative plan:** P07 Frontend Design Alignment Frozen Execution Plan.
- **Status:** P07-B complete at this checkpoint; P07-C is next.
- **Base SHA:** `118d3db50f7974c7197d84bdf6cfa7e3c8bb41ce`.
- **Base branch:** `dev` / `origin/dev`.
- **Execution branch:** `feature/p07-frontend-design-alignment-20260827`.
- **Milestone commits:** P07-00 `5d225230d3f71528538d7074e663af4b7083c218`;
  P07-A `dbf3f38227588965794e90748bf46a341832938f`; P07-B is the
  signed commit containing this checkpoint.
- **Current candidate:** P07-B working tree ready for its signed milestone commit.
- **Prerequisite integration:** the approved Agent 3 prerequisite-quality
  commits `6921219`, `d4c50c6`, and `118d3db` were explicitly authorized,
  fast-forwarded, and pushed to `dev` before this P07 branch was created.
- **Latest tests:** frontend test command pass (4 test files); typecheck pass;
  build pass; P06 public
  fixtures 1/1 at baseline; existing full-stack Playwright 5/5 with
  `MATERIAL_REVIEW_E2E_PASS` after P07-A; P07-B full-stack Playwright 6/6
  with `MATERIAL_REVIEW_E2E_PASS`.
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
- **Known limitations at this checkpoint:** production frontend does not yet
  consume P06 learning APIs; P07-C through P07-E remain to be implemented.
- **Next action:** create and push the signed P07-B commit, then implement P07-C
  StudySession lifecycle, direct refresh recovery, public single-choice
  Assessment, server feedback, completion, and reassessment/no-safe-item states.

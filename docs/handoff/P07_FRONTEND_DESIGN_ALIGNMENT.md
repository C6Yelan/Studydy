# Phase 07 Frontend Design Alignment

> Git and the frozen `/v1` public contract are authoritative when this document
> becomes stale. This matrix records the P07 implementation boundary; it is not
> a new product contract.

## Source priority

1. Production backend and frozen `/v1` OpenAPI on the P07 base.
2. P07 Frozen Execution Plan.
3. The 08.19 design package as the visual source of truth.
4. The 08.19 data-team prototype as interaction intent only.

The production frontend only projects public backend responses. It never derives
correctness, mastery, weakness, prerequisite gaps, adaptive priority, or a next
Concept.

## Baseline

- Base SHA: `118d3db50f7974c7197d84bdf6cfa7e3c8bb41ce`.
- Branch: `feature/p07-frontend-design-alignment-20260827`.
- Stack: React `19.2.8`, React DOM `19.2.8`, TypeScript `7.0.2`, Vite
  `8.2.1`, Playwright `1.62.1`.
- Existing canonical routes:
  - `/`
  - `/materials/:materialId/runs/:runId`
  - `/materials/:materialId/runs/:runId/knowledge-maps/:mapRevision`
- P07 route extension:
  - `/materials/:materialId/runs/:runId/knowledge-maps/:mapRevision/study-sessions/:studySessionId`
- The learning route carries only validated public opaque IDs. The current
  Concept, assessment, feedback, and adaptive panels remain inside the
  StudySession context instead of becoming separate fragile routes.

## Design and contract matrix

| Design / prototype | Design status | Current production truth | P07 decision | Classification |
|---|---|---|---|---|
| Login | Verified visual reference | No auth-form public contract; cookie session is created/refreshed by the app | Use only brand, form geometry, typography, and spacing as references; do not publish login | Reference / Unsupported |
| General AppShell | Verified visual language | All current routes need a stable product shell | Implement the approved 86–88 px header, 278–280 px desktop sidebar, blue/white hierarchy, compact responsive navigation, and only real destinations | Must |
| Home | Approved visual reference | `/` is the real PDF upload entry; no material-list API exists | Make upload the truthful first-use home inside AppShell; do not show fake totals or recent materials | Must / Contract adaptation |
| Upload | Upload-ready is a verified visual anchor | `POST /v1/materials` accepts a PDF; no subject/category fields exist | Preserve two-column upload/helper composition and selected-file row; omit fake three-level taxonomy | Must / Contract adaptation |
| Processing | Done is a verified visual anchor | One persisted `material_processing_run`; status is pending/running/succeeded/partial/failed | Preserve simplified processing shell, status timeline language, truthful page/result summary, and recovery states; never fake Concept/Relation counts | Must / Contract adaptation |
| Knowledge Map List / Overview | Verified 3×2 subject-card visual anchor | No list API and one route resolves one canonical Map | Reuse card geometry, spacing, icon language, and shell proportions in the current Map overview; do not hard-code six subjects or fake progress | Reference / Unsupported list |
| Knowledge Map Workspace | Focus/Review visual language is approved; prototype is interaction reference | `knowledge-map-view/v6` publishes Concepts, three Relation types, resources, Evidence, and canonical initial path | Implement Overview, Path, Focus, and Review as truthful views over one Map; Review means published review-required Map content until learner state is available in a StudySession | Must |
| Path | Approved sequential-journey language | `initial_learning_path` is canonical Agent 3 output | Label it `教材建議學習順序`; never mutate it from learner answers | Must |
| Focus | Approved selected-node and right-panel language | Only published Concepts and Relations may appear | Use a readable deterministic layout/list fallback with selected Concept/Relation detail; no raw candidates | Must |
| Review | Approved priority/review visual language | Pre-session Map has no learner weakness; StudySession weakness is backend output | Before a StudySession, show only Map quality/review needs. During a session, project Weakness without client scoring or fake percentages | Must / Contract adaptation |
| Concept Detail | Approved right Detail Panel language | Public Concept contains label, Claims, pages, Evidence, resources, and review status | Show only those fields; omit difficulty, importance, strength, and inferred confidence | Must |
| Relation Detail | Approved right Detail Panel language | Public Relation is `prerequisite`, `contains`, or `related`, with published Evidence references | Show type, endpoints, readable semantics, Evidence/page provenance, and cycle note where relevant; hide diagnostics/NLI internals | Must |
| Evidence | Prototype and panel reference | Public Evidence provides exact page and PDF bbox; original artifact is retrievable | Open original PDF at exact page; bbox is secondary metadata. Crop viewer is not required | Must / Fallback |
| Resource | Adjacent panel pattern only | Promoted public resources include title, URL, citation, license, use boundary, and page numbers | Show safe external action and public attribution; no resource synthesis | Must |
| StudySession | Missing dedicated approved mockup | Frozen APIs own lifecycle and current/deferred targets | Extrapolate from General UI cards and Knowledge Map panel language; student copy uses `本次學習` | Must / Missing-mockup extrapolation |
| Assessment | No approved dedicated production mockup | Only `single_choice`, exactly four public options; private answer stays server-side | Use one labeled radio group, one submit CTA, pending/conflict/no-safe-item states, and public feedback only | Must / Missing-mockup extrapolation |
| Feedback | Correct/wrong mascot and state language are references | Backend returns `is_correct`, public rationale, and Evidence IDs only after submit | Show result only after response; no answer key, correct option, private rationale, or generation provenance in client state/DOM | Must |
| Learning State | Old prototype uses fake mastery percentages | Backend returns four statuses plus separate band, confidence, coverage, trend, and data sufficiency | Keep those dimensions visually separate; never calculate a score | Must / Obsolete prototype semantics |
| Weakness | Old Review queue is visual reference | Backend distinguishes `observed_weak`, `needs_review`, `not_enough_data`, and published immediate prerequisite gaps | Project category, reason, confidence/data sufficiency, and remediation intent | Must / Contract adaptation |
| Adaptive Next Step | Prototype suggests learning actions | Backend publishes one StudySession-scoped plan/suggestion and exact apply revision | Render one `目前為你調整` primary card separate from canonical path; every action maps to a real route/capability | Must |
| Completion | Success state language is approved | `POST .../complete` returns a completed StudySession without permanent mastery | Show this learning session completed and allow a new isolated StudySession; do not claim permanent mastery | Must |
| Settings | Verified form reference | No current account/settings/export/delete public contracts | Use only as spacing/form reference; do not expose settings navigation or dead controls | Reference / Unsupported |
| Search | Present in design shell and fake prototype | No cross-material search API | Omit from production navigation/header. Local Map filtering is allowed only if it filters already-loaded public Concepts truthfully | Unsupported; optional real local filter |

## Frozen domain adaptations

- Production Relation types are exactly `prerequisite`, `contains`, and
  `related`.
- The old `similar`, `confusing`, `application`, and `example` taxonomy is
  obsolete and must not appear in production UI, fixtures, legends, or copy.
- `related` is symmetric and receives no one-way arrowhead.
- Directional marker treatment is reserved for `prerequisite` and `contains`.
- Raw Relation candidates and `relation_diagnostics` never enter the student
  layer.
- Canonical Initial Learning Path stays part of the Map. Adaptive Plan is a
  StudySession-scoped overlay rendered as a separate card.
- A new StudySession begins with no inherited learning state or weakness.
- The browser sends only assessment/question/selected-option/idempotency
  identity. Correctness is accepted only from public server feedback.

## Visual foundation

- General UI tokens use Studydy electric blue `#0757FF`, dark navy text,
  white/cool surfaces, 1 px cool-gray borders, restrained shadows, 7–13 px
  component radii, and the approved Inter/Noto Sans TC stack.
- Desktop proportions target the approved 1448 px reference. Narrow viewports
  collapse the sidebar and stack the Detail Panel without changing information
  order.
- Icons use one small inline thin-outline family; no icon framework is added.
- Mascots are direct, unchanged PNG assets from the approved runtime pack. CSS
  drawing, recoloring, mirroring, stretching, filtering, or image-model redraw
  is prohibited.

## Approved runtime asset shortlist

Only product-used PNGs are eligible for `frontend/public/assets/studydy/`:

| Product state | Approved runtime pose |
|---|---|
| Compact brand | General idle |
| Upload guidance | Guide holding PDF |
| Sidebar / gentle guidance | Welcome wave |
| Processing | Processing laptop |
| Processing complete | Success jump and compact completed pose |
| Failure | Failure/error pose |
| Empty | Empty/disappointed pose |
| Knowledge Map / study helper | Guide reading |
| Learning support | Reading/understanding pose |

No ZIP, spreadsheet, blind-test archive, prototype source, design archive, or
local design path may be committed.

## Baseline verification

| Evidence | Result |
|---|---|
| `frontend/npm test` | 3/3 passed |
| `frontend/npm run typecheck` | passed |
| `frontend/npm run build` | passed |
| P06 public fixture parser test | 1/1 passed |
| Existing full-stack Playwright harness | 5/5 passed; `MATERIAL_REVIEW_E2E_PASS` |
| Prototype walk-through | Login → Home → Upload ready → Processing → Done → Focus/Detail completed locally |

## Known approved deviations

- General UI Knowledge Map Overview is a six-subject list design, but production
  has no Map-list API. P07 applies its card geometry to the actual current Map.
- The old Focus/Review source contains obsolete Relation and learner metrics.
  P07 retains its layout/interaction language while replacing all semantics with
  frozen public output.
- Evidence uses original PDF + exact page rather than a new crop service.
- Dedicated Assessment/Adaptive mockups are absent; P07 extrapolates from
  approved General UI and Knowledge Map components.
- Full mobile polish is not claimed; narrow viewport no-blocking-break is the
  acceptance boundary.

# Phase 07 Design Acceptance

> This matrix is the reviewer entry point for P07. Screenshots are intentionally
> local-only and are not committed. Git, the frozen `/v1` contract, and automated
> test output remain the functional authority.

## Core screen matrix

| Screen | Design source | Contract source | Viewport | Functional | Visual | Accessibility | Deviation | Local screenshot |
|---|---|---|---:|---|---|---|---|---|
| AppShell | General UI desktop shell | Current canonical routes | 1280×720 | Pass | Reviewed | Header/main/nav landmarks; visible focus | Unsupported search/account controls omitted | `01_app_shell_desktop.png` |
| Upload ready | General UI Upload Ready verified anchor | `POST /v1/materials`, 100 MiB production limit | 1448×1086 | Pass | Reviewed | Labeled file input; error association | Three-level fake subject taxonomy omitted; backend size wins | `02_upload_ready.png` |
| Processing | General UI Processing Loading | `material-processing-run/v2` pending/running | 1448×1086 | Pass | Reviewed | `aria-live`; reduced motion | Indeterminate progress replaces unsupported fake percentage | `03_processing.png` |
| Processing done | General UI Processing Done verified anchor | Terminal run output binding | 1448×1086 | Pass | Reviewed | Semantic status/timeline | Contract-backed page count replaces fake 142/186/1 totals | `04_processing_done.png` |
| Map overview | General UI Knowledge Map verified card language | `knowledge-map-view/v6` Concepts | 1280×720 | Pass | Reviewed | Buttons expose full Concept names | One actual Map replaces unsupported six-subject Map list | `05_map_overview.png` |
| Concept focus | Knowledge Map Focus guideline/reference | Published Concepts and Relations only | 1280×921 | Pass | Reviewed | Keyboard-selectable Concept; labeled lines/legend | Readable deterministic relation rows replace unstable graph auto-layout | `06_map_focus_concept.png` |
| Relation detail | Knowledge Map Relation Detail reference | `FormalRelationView` + published Evidence | 1280×921 | Pass | Reviewed | Named panel/close; Relation text is not color-only | Old strength/confidence/NLI diagnostics omitted | `07_relation_detail.png` |
| Initial path | Knowledge Map Path language | Canonical `initial_learning_path` | 1280×783 | Pass | Reviewed | Ordered semantic list | No fake locked/completed states before StudySession | `08_initial_path.png` |
| Study current Concept | General UI + Knowledge Map panel extrapolation | StudySession/context + Map | 1280×1528 | Pass | Reviewed | Main/aside headings; exact Evidence actions | Dedicated approved learning mockup unavailable | `09_study_current_concept.png` |
| Assessment | General component language extrapolation | Public `single_choice`, exactly four options | 1280×1867 | Pass | Reviewed | Fieldset, legend, labeled radios, disabled submit | No approved dedicated Assessment mockup | `10_assessment.png` |
| Feedback | General success/warning language | Public `answer-feedback/v1` | 1280×1759 | Pass | Reviewed | Live feedback and Evidence actions | Only server public rationale shown | `11_feedback.png` |
| Learning State / Weakness | Knowledge Map Review language adapted | `learning-state/v1`, `weakness/v1` | 620×478 component | Pass | Reviewed | Separate named dimensions and readable category copy | Fake mastery percentage removed | `12_learning_state_weakness.png` |
| Adaptive Next Step | General card + Knowledge Map language | `adaptive-response/v1` | 620×304 component | Pass | Reviewed | One primary action with full text | Card overlay used instead of a second graph | `13_adaptive_next_step.png` |
| Prerequisite remediation | Knowledge Map Path + adaptive extrapolation | Published `prerequisite`, exact plan apply | 1280×1602 | Pass | Reviewed | Current/deferred copy and landmarks | Public fixture used because real sample coverage is absent | `14_prerequisite_remediation.png` |
| Completed StudySession | General success language | Completed `study-session/v1` | 1280×720 | Pass | Reviewed | Recovery action to Map | Copy avoids permanent-mastery claim | `15_completed_session.png` |
| Retryable failure | General failure language | Retryable public API error | 1280×720 | Pass | Reviewed | Alert + real retry action | Uses generic approved failure pose | `16_failure_retryable.png` |
| Empty Map | General Empty Data language | Failed/empty public Map | 1280×720 | Pass | Reviewed | Named empty state + processing recovery | Original empty asset; no invented content | `17_empty.png` |
| Narrow viewport | General desktop system, conservative responsive fallback | Upload public contract | 390×1586 | Pass | Reviewed | No horizontal overflow; controls remain labeled | Functional narrow layout, not a claimed mobile design system | `18_narrow_viewport.png` |

## State matrix

| State | Evidence |
|---|---|
| loading | App session, Map, StudySession, assessment, processing views use polite live regions |
| empty | Empty Map and no-current-Concept states have recovery actions |
| success | Processing done, correct feedback, completed StudySession |
| partial | Partial processing/Map banner and excluded-page Review projection |
| insufficient data | `not_enough_data`, `needs_more_data`, and no-safe-item UI |
| stale revision/idempotency | Conflict stops client scoring/retry loop and offers session refresh |
| retryable failure | Material status read can recover in place |
| fatal failure | Terminal material failure returns to upload without fake retry API |
| app session expired | Parallel session refresh is single-flight; missing cookie session is recreated |
| no active StudySession/current Concept | Safe empty StudySession view with Map recovery |
| active StudySession | Current Concept, Evidence, assessment, state, weakness, adaptive action |
| completed StudySession | Session-scoped completion copy and Map recovery |
| new StudySession reset | Session B starts `not_started` and does not show Session A mastery/weakness |
| no safe assessment | Student-readable fallback; no answer inference |
| Map empty/partial/large | Automated 0-Concept, excluded-page, and 30-Concept cases |

## Accessibility checklist

- Semantic application header, main, aside, and named navigation landmarks.
- One clear page H1 per top-level product view.
- Map tabs implement tab/tabpanel relationships, roving tab stop, Arrow Left /
  Arrow Right / Home / End keyboard behavior.
- File input has an accessible label and associates validation errors through
  `aria-describedby` / `aria-invalid`.
- Assessment uses fieldset/legend and exactly four labeled native radio inputs.
- Async loading and feedback use live regions; errors use alerts.
- Focus indicators are visible and component geometry does not jump on focus.
- Relation type is communicated by visible label, line style, and marker;
  `related` is dashed, symmetric, and has no one-way arrowhead.
- Reduced-motion preference disables nonessential animation.
- Desktop 1280/1448 and narrow 390 checks have no blocking horizontal overflow;
  assessment options remain fully inside the viewport.

## Contract and privacy adaptations

- Production Relation types are exactly `prerequisite`, `contains`, and
  `related`; old `similar`, `confusing`, `application`, and `example` UI is
  obsolete.
- General UI's 3×2 subject overview is a visual reference only because there is
  no Map-list API. Actual Concept cards remain data-driven.
- Original PDF + exact page is the approved Evidence viewer fallback; no crop
  service or private runtime path is introduced.
- Login, Settings, account security, data export/delete, and global search stay
  unsupported and absent from active navigation.
- Assessment public parsers reject extra answer/private-generation fields.
- Screenshots contain only public/non-private fixtures. No design archive,
  workbook, ZIP, private material, or local design path is committed.

## Verification summary

- Frontend test command: pass (4 test files, including strict contract/leak and
  Relation presentation assertions).
- TypeScript typecheck: pass.
- Production Vite build: pass.
- Playwright full-stack harness: 14/14, `MATERIAL_REVIEW_E2E_PASS`.
- Frozen backend API runtime + public fixtures: 16/16 pass.
- Acceptance screenshots: 18/18 generated and visually inspected.
- Session isolation: pass.
- Public answer leakage: 0 fields in production source and build.
- Raw Relation diagnostics in student features: 0.

## Approved remaining limitations

- Evidence opens the original PDF at the exact page; region crop rendering is
  not implemented.
- Desktop/laptop is the high-fidelity target. Narrow viewport is non-blocking,
  but no separate approved mobile visual system exists.
- Optional mascot animation is not implemented; approved static PNGs are used.
- Real-material prerequisite publication remains an upstream sample-coverage
  limitation; the public canonical fixture proves the UI remediation flow.

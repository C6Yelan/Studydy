# Studydy final architecture

Production has one semantic path:

```text
PDF → native Evidence / optional OCR → document sections + Evidence bundle
    → resident Gemma 4 unified semantics → deterministic projection
    → Document Tree + canonical Concepts + typed Relations + Initial Path
    → StudySession + Assessment + learner guidance
```

Supplementary resource recommendation (Agent 2) is removed. Concepts retain only the uploaded
material's Evidence and PDF locators. Knowledge Structure and its public view use schema v2, with
no resource-library fields or separate resource PDF kind. Fresh pre-release databases use the
initial migration followed by additive learner-credentials and material-name migrations; historical evaluation
artifacts remain separate and are not rewritten.

Gemma 4 owns Concept boundaries, Claim meaning, cross-section consolidation, Relation proposals/reasons,
and Assessment semantics. Code owns source identity, Evidence/span binding, exact technical literals,
schema, ownership, endpoints, duplicates/conflicts, prerequisite cycles, private answers, scoring,
and stale/idempotency/concurrency behavior.

Material requests retain document-global integer handles, page, kind, and exact text under section
titles. Response v4 Claims select whole Evidence handles with `s: [handle, ...]`; character offsets
are not accepted. Native Evidence joins geometrically consecutive lines within a PDF text block or a wrapped
continuation across blocks, respecting heading levels, columns and new list items while preserving
line breaks and bounding boxes. A null meaning reuses the
selected units. Code expands quotes and canonical references; technical-literal protection still
applies, but partial quotations cannot replace a complete meaning.

Page processing policy `native-first-page-evidence/v7` combines these native Evidence units with
OCR for substantial image regions that have little native text coverage. Readable native text alone
does not establish page completeness. Mixed pages retain their native units and add OCR text from
the uncovered image regions; unrecovered image content retains a review status.

Original Evidence remains available. Claim candidates omit explicit copyright text in page margins,
repeated marginal running text, and page numbers consistent with page order across pages. Headings,
code-like text and non-margin content are preserved; an arbitrary bottom crop is not used. Filtered
handles are never renumbered. These rules reduce known citation failures, not prove semantic
support for every retained body-text citation.
Formal `::=` definitions keep their indented bodies and start a new unit at the next definition.
An unsupported literal string `null` is rejected as a Claim; source-backed null terminology and
valid literal-restored content remain supported. Contrast relations retain their proposed endpoint
order so positional explanations stay consistent; reverse duplicates are still removed.

Bundles are packed using the resident tokenizer with the actual prompt and current Concept catalog,
reserving 4096 output tokens within the unchanged 32768-token context. New Evidence per bundle
is bounded to 1536 input tokens without the existing Concept catalog, so longer documents make
incremental progress without forcing their full semantic output into one response. An indivisible
Evidence block may exceed this soft limit if the full request still fits the model context. A truncated response fails;
it does not count as a successful material or trigger additional split calls.
Material generation explicitly pins the existing thinking/xhigh template and sampling settings in
the runtime lock; packing and inference use the same template options. Relation instructions retain
supported edges while distinguishing necessary dependencies, concrete uses, and the entities being
compared.

Assessment generates three candidates with the v2 response contract, then makes one bounded batch
check through the same resident Gemma 4 service. The checker receives source Evidence and reordered
options without the proposed answer key. Publication requires a unique selected answer matching
the generator's exact source span, and no duplicate of a prior question. Rewording the same task,
referent and conditions is a duplicate; different requested attributes, referents or application
scenarios can assess the same knowledge. Distractors may occur elsewhere in Evidence.
Code retains exact source binding, option identities, private answers, scoring and idempotency.
Novelty and angle remain legacy provenance fields, with no veto over new checked items' mastery
eligibility. Existing stored eligibility remains unchanged. Each Claim still needs two distinct
eligible correct items and a correct latest answer. Provenance v6 records the blind-check option
order and verdict under source-span-single-choice/v5; legacy v5 provenance remains readable.
Generation and checking both disable thinking and have fixed output budgets. Material generation
retains its pinned thinking/xhigh settings; all tasks share the same resident service and model.

The only Relation types are `prerequisite`, `part_of`, `application`, `example`, and `contrast`.
`prerequisite` is the only Relation that can change Initial Path order or create a learner prerequisite
gap. Document Tree placement always comes from document structure.

There is one production Python minor (3.12), one externally resident
`google/gemma-4-31B-it-qat-w4a16-ct` service, and one optional Unlimited-OCR child. The backend never starts,
stops, swaps, or unloads Gemma 4. Assessment uses the same authenticated loopback service.
mDeBERTa is removed.

Pre-release persistence is a clean final schema. `knowledge_structures` stores one immutable artifact
instead of parallel material/map artifacts. The credentials migration upgrades the accepted schema
without rewriting stored artifacts.

Account credentials live on `learners`; `learner_sessions` remains the authorization authority.
Registration creates one learner and session atomically. Login verifies the salted scrypt password
hash and issues a new session for the same learner. Logout revokes only that session. Existing
anonymous learners remain intact and are not automatically attached to accounts. All API/PDF
responses are private and `no-store`. The frontend retires its client and unmounts private views on
logout or session expiry; it never creates anonymous identities or replays failed writes under a
new identity. The browser-global latest-material pointer and its consumers are removed.

The material library is a read projection over Material, Artifact, MaterialProcessingRun and
KnowledgeStructure. Materials retain an optional uploaded display name; older rows have a
recognizable date/ID label. Latest attempts and published revisions are listed independently, so a
failed new attempt cannot hide a prior result. Reopen uses existing exact-revision GET endpoints
and creates no learning records. There is no separate material-history store.

Study resume is a read projection of the existing StudySession, exact KnowledgeStructure,
Assessment and AnswerEvent. It uses the existing assessment/event validators, feedback projection
and derived progress. The material library exposes one canonical persistent state per structure revision; the learner hub opens only the latest usable structure. Question
selection is explicit in the browser URL. Reads never create sessions/questions/answers or apply
guidance. Completed sessions remain readable, and feedback is exposed only for a validated saved
AnswerEvent. There is no additional history table or mastery calculation.

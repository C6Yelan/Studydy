# Studydy final architecture

Production has one semantic path:

```text
PDF → native Evidence / optional OCR → document sections + Evidence bundle
    → resident Gemma 4 unified semantics → deterministic projection
    → Document Tree + canonical Concepts + typed Relations + Initial Path
    → StudySession + AssessmentSet + AnswerEvent
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

教材 worker 在初始語意分析後執行 [檢核與整理](material-review.md)，再發布 canonical 地圖。
它共用既有 semantic transport；既有教材可重用已保存 Evidence 建立整理版本。原版地圖與舊作答不覆寫。

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
B5-Q 的安全候選品質排序、provenance v8 與 source-span-single-choice/v6 見
[assessment-quality.md](assessment-quality.md)。品質提示不設最低分；舊題資料不改寫。
每個 Claim 仍須兩道不同合格正確題且最新作答正確，單次答對不代表掌握。

B5-D 將一個 Concept 的多個重點預先準備成持久題組。計畫決定動態題數，現有 worker
逐題在 DB 交易外生成並驗證，最後原子發布；失敗只明確重試未完成題，或選擇部分發布。
`assessment_sets`／`assessment_set_items` 保存計畫、狀態與成員，作答沿用 AnswerEvent。
詳見 [單一觀念題組](assessment-sets.md)。B5-R 以同一題組加上初篩關聯與複習決策，
只對已複習的錯誤重點產生補強；一次新題答對可結束該點本次補強。
補強作答在投影中保留 assisted 身分，不累計或恢復獨立掌握證據；地圖、進度與歷史共用
本輪結果。詳見 [錯題補強](assessment-remediation.md)。

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

Study resume (`study-resume/v4`) projects the bound StudySession, KnowledgeStructure,
AssessmentSet summaries and derived learner progress. The selected set is explicit in the
Study Session URL. Published questions and feedback are read through the selected set, using
its membership and private-answer validators. Reads never create sessions, questions or answers.
The old single-question generation/submission/history and guidance-apply routes are removed.
There is no separate preparation page or mastery calculation.

Material creation uses the source collection and revision APIs (`/v2/materials`, sources,
revisions); direct `/v1/materials` POST and `/v1/material-processing-runs` POST are retired.
Public readers accept the current source-aware view/run/library contracts only. Historical
migration files and persisted rows are not deleted or rewritten by this cleanup.

B3-A appends immutable source snapshots and analyzes only added sources, retaining verified prior
Evidence and semantic content. PDFs remain separate, with source-aware reading positions in a v4
KnowledgeStructure. Unchanged, unambiguous Claims can inherit existing answer evidence through the
same learning-state reducer without copying or rewriting AnswerEvents. Valid updates containing new
grounded Claims promote the head, including partial results with quality notices. Cancellation,
processing failures and updates without usable added content retain the previous head. Unreferenced
old structures are pruned, while structures required by saved learning remain readable. See
[source revisions](source-revisions.md) for publication, retention and migration details.

B3-B creates the initial map from one or more ordered, ready sources using the same pipeline.
All uploads enter the source confirmation page before semantic analysis; per-file retries reuse
their upload receipts. Initial source ordering is frozen by the revision request, with no second
KnowledgeStructure schema or merged PDF.

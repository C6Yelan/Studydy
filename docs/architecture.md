# Studydy final architecture

Studydy 自有的現行 schema、政策與 API 契約統一為 v1，採用每種契約最新資料形狀。
Backend、frontend、runtime lock 與資料庫只接受此契約；第三方 API 路徑、套件／模型 revision
及 optimistic concurrency 的 `set_version` 不屬於這次版本重整。

`build_structure_draft()` 產生未綁來源的內部草稿，沒有正式 schema 或 revision。
`finalize_knowledge_structure()` 在來源集合綁定後產生唯一的 `knowledge-structure/v1`，
revision 包含正式契約與 binding；舊 revision 不能只換 schema 標籤沿用。持久層拒絕未標 schema
或其他版本的結構。語意推論只走 HTTP；runtime-binding v1 保存該次 HTTP 模型與服務身分。

語意模型 ID／revision 由執行設定指定。教材以該 run 保存的 runtime lock／binding 核對 provenance，
題目以生成它的題組保存的 runtime lock 核對模型、policy 與 prompt hashes；
教材模型與出題模型可以不同。結構 validator 保護資料形狀、內容 revision 與來源關係，
不把某個模型名稱當成內容有效性的條件；不提供舊版本 reader 或缺少快照時的放行分支。
服務 preflight 仍檢查已設定模型的 discovery、server version、context 與 tokenizer，
模型實際權重 revision 仍須部署證據確認。換模型需另行完成品質驗收，Gemma 的既有驗收不自動適用。

一般地圖／學習讀取在同一 DB snapshot 核對保存結構、runtime 與來源集合 metadata，
不為顯示已存內容掃描所有原檔。來源發布時完整核對 original／normalized／mapping bytes；
下載、PDF 預覽、分析輸入及 Evidence mapping 使用時，各自完整驗證實際開啟的檔案。
因此原檔損毀不會讓保存地圖消失，但使用損毀檔案或發布新結果必須失敗。
題組設定在同一次交易中共用已驗證的 provenance，prior question 直接傳遞已驗證結果；
沒有跨請求 hash 快取，內容 revision、私密答案、冪等與 checkpoint 檢查保留。

| Artifact 操作 | 路由 | 邊界與回應 |
|---|---|---|
| normalized PDF 預覽 | `GET /v1/artifacts/{id}` | owner 與 normalized 類型核對、PDF、內容長度與 ETag |
| 原檔下載 | `GET /v1/artifacts/{id}/download` | owner 與 original 類型核對、原 MIME、attachment 檔名、nosniff |

兩者均為 private/no-store，不允許拿另一種 artifact ID 代替；舊 `/v2` 路徑不提供 alias。
版本切換的資料保留、衍生資料清理與備份見 [本地環境](local-environment.md)。

Production has one semantic path:

```text
PDF → native Evidence / optional OCR → document sections + Evidence bundle
    → resident Gemma 4 unified semantics → deterministic projection
    → Document Tree + canonical Concepts + typed Relations + Initial Path
    → StudySession + AssessmentSet + AnswerEvent
```

Supplementary resource recommendation (Agent 2) is removed. Concepts retain only the uploaded
material's Evidence and PDF locators. Knowledge Structure and its public view use schema v1, with
no resource-library fields or separate resource PDF kind. Fresh pre-release databases use the
four domain baselines: identity/materials, sources/processing, learning/answers, and assessment sets.
Historical evaluation artifacts remain separate and are not rewritten.

Gemma 4 owns Concept boundaries, Claim meaning, cross-section consolidation, Relation proposals/reasons,
and Assessment semantics. Code owns source identity, Evidence/span binding, exact technical literals,
schema, ownership, endpoints, duplicates/conflicts, prerequisite cycles, private answers, scoring,
and stale/idempotency/concurrency behavior.

教材 worker 在初始語意分析後執行 [檢核與整理](material-review.md)，再發布 canonical 地圖。
它共用既有 semantic transport；既有教材可重用已保存 Evidence 建立整理版本。原版地圖與舊作答不覆寫。

Material requests retain document-global integer handles, page, kind, and exact text under section
titles. Response v1 Claims select whole Evidence handles with `s: [handle, ...]`; character offsets
are not accepted. Native Evidence joins geometrically consecutive lines within a PDF text block or a wrapped
continuation across blocks, respecting heading levels, columns and new list items while preserving
line breaks and bounding boxes. A null meaning reuses the
selected units. Code expands quotes and canonical references; technical-literal protection still
applies, but partial quotations cannot replace a complete meaning.

Page processing policy `native-first-page-evidence/v1` combines these native Evidence units with
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

Assessment generates three candidates with the v1 response contract, then makes one bounded batch
check through the same resident Gemma 4 service. The checker receives source Evidence and reordered
options without the proposed answer key. Publication requires a unique selected answer matching
the generator's exact source span, and no duplicate of a prior question. Rewording the same task,
referent and conditions is a duplicate; different requested attributes, referents or application
scenarios can assess the same knowledge. Distractors may occur elsewhere in Evidence.
Code retains exact source binding, option identities, private answers, scoring and idempotency.
B5-Q 的安全候選品質排序、provenance v1 與 source-span-single-choice/v1 見
[assessment-quality.md](assessment-quality.md)。品質提示不設最低分；舊題資料不改寫。
每個 Claim 仍須兩道不同合格正確題且最新作答正確，單次答對不代表掌握。

B5-D 將一個 Concept 的多個重點預先準備成持久題組。計畫決定動態題數，現有 worker
逐題在 DB 交易外生成並驗證，最後原子發布；失敗只明確重試未完成題，或選擇部分發布。
`assessment_sets`／`assessment_set_items` 保存計畫、狀態與成員，作答沿用 AnswerEvent。
詳見 [單一觀念題組](assessment-sets.md)。B5-R 以同一題組保留初篩關聯，
直接對所有目前待補強的錯誤重點產生補強；一次新題答對可結束該點本次補強。
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
instead of parallel material/map artifacts. Baseline installation directly creates the final Email
and source-aware schema; adopting it for an existing database preserves all product records.

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

Study resume (`study-resume/v1`) projects the bound StudySession, KnowledgeStructure,
AssessmentSet summaries and derived learner progress. The selected set is explicit in the
Study Session URL. Published questions and feedback are read through the selected set, using
its membership and private-answer validators. Reads never create sessions, questions or answers.
Single-question generation/submission/history routes are removed.
Completed-cycle navigation applies the current backend `advance` or `complete` decision through
`POST /v1/study-sessions/{id}/guidance/apply` (`guidance-apply/v1`). The revision is checked under
the shared Material/Study lock; replay cannot advance twice, and active assessments remain protected.
There is no separate preparation page or mastery calculation.

Material creation uses the source collection and revision APIs (`/v1/materials`, sources,
revisions); direct `/v1/materials` POST and `/v1/material-processing-runs` POST are retired.
Public readers accept the current source-aware view/run/library contracts only. Persisted product
rows are retained; pre-release migration history was consolidated into four domain baselines.

B3-A appends immutable source snapshots and analyzes only added sources, retaining verified prior
Evidence and semantic content. PDFs remain separate, with source-aware reading positions in a v1
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

Knowledge Map reads material metadata alongside its immutable structure and run binding. If a study
exists, it reads the small learner-progress response rather than fetching the full StudySession
resume envelope again. Progress and resume share a repeatable-read database snapshot: source-bound
structure validation, study scope, AnswerEvents, assisted evidence and cycle projection are reused
within that read. No global cache replaces owner or source checks.

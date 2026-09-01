# Phase 06 — Agent 4 Learning Adaptation：Assessment、Learning State、Weakness 與 Dynamic Suggestion — Frozen Execution Plan

- **文件定位：** Phase 06 的 authoritative product / execution plan。P06-00～03 已完成並凍結；P06-04～10 依本文件與最新 `dev` 真實程式碼繼續實作。
- **目前 frozen baseline：** P06-03 accepted candidate `161cf9f6761e187ba944b9b71b96ddea454f09df`。P06-04 long-run 只可在該 candidate 正式合回最新 `dev` 後開始；若實際 `dev` 已不同，先確認差異來源。
- **前置依賴：** Phase 05 已發布的 Map v10 Document Tree、獨立 `initial_learning_path` 與 positive-only `prerequisite_constraints` contract；Phase 03 stable Evidence；Phase 04 promoted supplementary resources。
- **核心原則：** Agent 4 不重建 Knowledge Map、不創造新的 Relation、不覆寫 Agent 3 canonical `initial_learning_path`；以 evidence-grounded 單選題建立可信 assessment signal，再建立 StudySession-scoped learner state、weakness 與 adaptive next-step overlay。
- **Assessment scope：** V2 第一版固定只支援 `single_choice`，**exactly 4 options**；free-response、複選、程式題與 AI semantic grading 全部延後。
- **Assessment grounding：** 第一版題目固定為 **Formal Concept / Claim / canonical exact Evidence-grounded**；不新增 Relation-question contract。
- **Session 邊界：** Learning State / Mastery / Weakness / Adaptive Plan 只在單次 `StudySession` 內成立；不同 StudySession 預設互不繼承。
- **命名凍結：** `LearnerSession` = cookie/auth session；學習循環 production entity = `StudySession` / `study_sessions`，不得混用。
- **Mastery 原則：** claim-aware、deterministic、conservative；單題答對永遠不得直接 mastered，confidence / coverage 與 mastery 分離。
- **訊號限制：** V2 **不使用 dwell time / engagement duration 作 learner-state signal**；不足時使用 `needs_more_data`、`collect_more_data` 或 canonical path fallback。
- **Prerequisite 原則：** 只使用 positive-only `prerequisite_constraints`；`contains` / `related` 永不作 Agent 4 consumer input。
- **Adaptive output：** 一次只輸出 **one primary adaptive step**；Suggestion 只是 Adaptive Plan projection，不建立第二套 recommendation engine。
- **Execution mode：** P06-04～10 可在同一 Codex App conversation / long-run Goal 連續執行，但每個 Task 必須獨立 test → commit → push → checkpoint；重大 contract / architecture 衝突才升級給 planner/reviewer。

## 階段定位

Agent 4 的產品責任是「學習檢測、學習狀態分析與動態學習調整」，不是另一個 Knowledge Map Agent，也不是只負責生成題目的題庫 Agent。

V2 第一版以 **evidence-grounded `single_choice` Assessment → deterministic scoring → trusted AnswerEvent → session-scoped Learning State → Weakness / prerequisite gap → Adaptive Plan → reassessment** 建立可信閉環。P06-03 已證明 Assessment Generation production direction 可行；後續 P06-04～10 不再重新研究產題架構，除非真實 integration evidence 證明 frozen contract 無法成立。

核心閉環：

```text
Published Knowledge Map / inline Initial Learning Path
+
StudySession
→ Learn current Concept / Resource
→ Evidence-grounded single-choice Assessment
→ User selects option
→ Server-side deterministic scoring
→ Trusted AnswerEvent
→ Session-scoped Learning State / Mastery / Confidence / Coverage
→ Weakness / immediate prerequisite gap
→ One-primary-step Adaptive Plan
→ Suggestion projection
→ Review / Practice / Relearn prerequisite / Next Concept
→ Reassessment with a new safe item
→ State / next-step update
→ Repeat inside the same StudySession
```

Canonical Map / inline `initial_learning_path` 必須保持可回溯；個人化只以 StudySession-scoped derived artifact 表示，不 in-place 修改 Agent 3 產物。StudySession 結束後可保存歷史資料作診斷 / Demo evidence，但 V2 不自動合併成跨 session 永久 learner profile。

## 階段目標

把 V1 deterministic learning flow 深化成真正具有學習價值、可驗證、可解釋的 Agent 4：

- 由 Formal Concept / Claim / canonical exact Evidence 產生 validator-approved `single_choice` Assessment；exactly 4 options，answer key 永遠 private。
- remediation / reassessment 能 deterministic 選擇尚未使用的安全題目；安全候選耗盡時 fail closed，不降低 Gate。
- submission 只接受必要 identity + selected option；server-side deterministic scoring，產生 trusted AnswerEvent。
- `mastery_estimate` / mastery band 與 `confidence / evidence_coverage` 分離。
- Learning State 只由 StudySession 內可信事件 deterministic 推導；不使用 dwell time。
- claim-aware Mastery 必須符合 frozen conservative rule，不以單題或低 coverage 假裝 mastered。
- 找出 observed weakness / needs review / not enough data / possible immediate prerequisite gap。
- 依 positive-only `prerequisite_constraints` 與獨立 `initial_learning_path` 建立 learner-specific Adaptive Plan overlay。
- Adaptive Plan 一次只輸出 one primary step；Suggestion 只做學生可理解 projection。
- 新 StudySession 預設從 `not_started` / `needs_more_data` 開始，不沿用前一 session 的 Mastery / Weakness / Adaptive Plan。
- Map、Concept、Evidence、Resource 或 `prerequisite_constraints` 資料不足時明確 fallback，不猜測、不建立虛假 precision。

## 為什麼現在做

Phase 05 後，Knowledge Map 已更新為正式發布的 **Map v10 Document Tree**；
`initial_learning_path` 是獨立的已發布路徑，prerequisite 依賴則以 positive-only
`prerequisite_constraints` 表達。

因此 Agent 4 必須直接消費已發布的 Map v10 Document Tree、獨立
`initial_learning_path` 與 `prerequisite_constraints`，不能消費 `related` / `contains`、
Relation graph、raw relation candidate、verifier diagnostics，或自行推論新的 dependency。

同時，舊 V1 每 Concept 題目供給與 mastery confidence 規則不足，若沒有可信 learner signals，就無法真正做到「依使用者學習狀況調整後續學習」。

## Agent 4 的正式邊界

### Agent 4 可以做

- 讀取 Map v10 Document Tree、Formal Concept、Claim、Evidence、獨立
  `initial_learning_path` 與 positive-only `prerequisite_constraints`。
- 建立 / 消費 server-bound `StudySession` context。
- 根據 Concept / Claim / Evidence 產生 `single_choice` 單選題與 distractors，並經 validator 後發布。
- 執行 server-side deterministic scoring，產生 trusted assessment event。
- 讀取 learner-scoped trusted events。
- 計算 Concept learning state / mastery / confidence。
- 判斷 observed weakness。
- 只使用 positive-only `prerequisite_constraints` 檢查 prerequisite gap。
- 建立 learner-specific adaptive plan overlay。
- 選擇下一個 Concept / review / practice / resource action。
- 解釋「為什麼現在建議做這件事」。

### Agent 4 不可以做

- 新增、刪除或重新分類 Agent 3 Relation。
- 把 `related` 自行解釋為 prerequisite、confusing、application、example 等舊語意。
- 修改 canonical Knowledge Map revision。
- 覆寫 Agent 3 的 `initial_learning_path`。
- 使用 raw candidate pairs、relation diagnostics 或 verifier rejected proposal 作學習依賴。
- 由 client 傳入 mastery、score、correctness、learner identity 或 recommendation decision。
- 只因一次答錯或一次答對就決定 weakness/mastered；V2 不使用停留時間作 learner-state signal。
- 生成 free-response、申論、程式題、複選題等第一版未支援題型，或使用 LLM semantic grading 決定正誤。
- 自動把前一個 `StudySession` 的 Mastery / Weakness / Adaptive Plan 合併到新 session。
- 建立帳號層級長期 learner profile、spaced repetition 排程或跨裝置同步。

## 本階段預期成果

### A. Published Knowledge Map consumption contract

Agent 4 只使用正式 public/product artifact：

- Formal Concepts。
- Concept claims / Evidence / source pages。
- promoted `supplementary_resources`。
- Map v10 Document Tree。
- 獨立的 `initial_learning_path`。
- positive-only `prerequisite_constraints`。
- Knowledge Map revision / material identity。

使用規則：

- **`prerequisite_constraints`**：唯一可作為 hard learning dependency / prerequisite-gap 依據的 positive-only constraints。
- `related` / `contains` 與 Relation graph 不進 Agent 4 consumer path。
- raw relation candidates / diagnostics 不進 learner decision path。

### B. StudySession contract

Agent 4 V2 的 learner state 以單次 `StudySession` 為正式邊界，與 auth `LearnerSession` 完全分離。P06-01 已完成此 production foundation。

StudySession 至少綁定：

- `study_session_id`（server 建立）。
- server-bound `learner_id`。
- `material_id`。
- frozen exact Knowledge Map revision；inline `initial_learning_path` 隨該 Map revision 綁定，**不存在 standalone LearningPath producer/revision**。
- lifecycle：`active` / `completed`；只有真實需求才新增其他狀態。
- current Formal Concept / current target（若保存，必須 server-validated）。
- assessment / trusted event / derived state lineage。

規則：

- 同一 StudySession 內允許反覆「學習 → 出題 → 作答 → 補救 → 再測 → 更新下一步」。
- 新 StudySession 不繼承前一 session 的 mastery estimate、weakness 或 adaptive routing。
- 可保存舊 session 作歷史紀錄，但**保存不等於沿用其 learner-state inference**。
- StudySession 與 Knowledge Map revision 必須可追 lineage；stale / wrong owner / wrong material / wrong Map revision fail closed。
- 不建立帳號層級 learner profile、跨裝置同步或 long-term history aggregation。

### C. Assessment Generation / Practice capability v2 — **P06-02 / P06-03 Frozen**

Assessment 是 Agent 4 建立 Mastery evidence 的正式子能力。P06-02 / 03 已完成並凍結，後續 Task 不重新設計此 contract。

#### Public / private contract

- `question_type = single_choice`。
- **exactly 4 options**，opaque option IDs。
- stable question / assessment identity。
- `target_formal_concept_id`。
- `target_claim_id`。
- actual `source_evidence_ids` subset。
- public：prompt / options / Concept / Claim / Evidence refs / policy identity；**不含答案**。
- private：correct option、rationale、NLI / generation provenance。
- P06 第一版 Assessment **Claim / Evidence-grounded only**；不新增 Relation-question refs。

#### Canonical Evidence handoff

- `study-material-output/v5` durable 保存 canonical exact Evidence text。
- Agent 4 依 owner / material / source output / Evidence identity 嚴格 join。
- Agent 4 不重新 OCR、不重新解讀 PDF、不從 obsolete cache 重建 Evidence。
- wrong owner / wrong material / missing Evidence / binding mismatch / tamper 全部 fail closed。

#### Frozen production generation strategy

```text
Formal Concept / Claim / canonical exact Evidence
→ Qwen3-14B bounded structured proposal (3 candidates)
→ deterministic structural / duplicate / leakage gates
→ selected-Evidence NLI grounding Gate
→ full-Claim NLI margin Gate
→ full-Claim maximum distractor risk check
   ├─ < 0.4 → safe candidate path
   └─ >= 0.4 → bounded repair
                → 5 mutation proposals
                → deterministic mutation proofs
                → retain safe distractors
                → selected/full NLI recheck
→ deterministic new-item selection
→ deterministic rationale from actual selected Evidence
→ P06-02 Assessment builder
→ private provenance
→ publish / store or reject
```

Frozen safety rules：

- selected Evidence subset 必須自身支持 correct option：relative entailment margin `>= 0.1`。
- full Claim Evidence 必須維持 correct relative margin `>= 0.1`。
- full Claim maximum distractor entailment `>= 0.4` 時，不可直接 promotion，必須進 repair。
- verifier complete Evidence-option pair 最大 384 tokens；`truncation=False`，超限在 inference 前 reject。
- repair 無 passing item時不回退 risky proposal；繼續考慮其他 ranked safe proposals。
- 同 StudySession / Claim 已用 question identity 不作新題；所有安全 identities 耗盡才 `ASSESSMENT_NO_NEW_SAFE_ITEM`。
- model `support_ids` 只是 proposal，不是 grounding authority。
- 任一必要 semantic condition無法證明時，不生成 Assessment。
- no same-model critic、blind verifier、absolute-NLI fallback、4B/8B substitute、relation-question fallback。

Runtime responsibility：

- Agent 1–3 material runtime：`studydy-local-ai-runtime-lock/v4`。
- Agent 4 assessment runtime：獨立 `studydy-assessment-runtime-lock/v1`；目前 generation policy = `assessment-generation-policy/v2`。
- Assessment policy / code 變更不得改變 Material runtime identity。

Accepted P06-03 qualification baseline（不得因後續 Task 偷偷降低）：

- Representative：24/30 = 80%，critical promotion = 0。
- High-risk holdout：critical promotion = 0。
- Multiple-supported challenges：unsafe promotion = 0。
- P06-02 contract：所有 promoted items通過。
- selected Evidence grounding：所有 promoted items通過。
- public answer leakage = 0。

Known limitation：direct cold Assessment lifecycle 約 135 秒；這是 P06-08 前需處理/評估的 usability issue，但不是重新開啟 P06-03 generation research 的理由。

### D. Assessment quality Gate — **Frozen**

- answerable from selected canonical Evidence。
- selected Evidence 本身必須支持 correct answer；full Claim Evidence 用於完整 ambiguity / multiple-support risk 判斷。
- no answer-key leakage。
- exactly one defensible answer；無重複 option / multiple reasonable correct answers。
- wording 適合學生，不把答案直接洩漏在題幹。
- item identity / used-question behavior可追蹤，支援同 StudySession reassessment。
- complete verifier input超出 qualified token boundary時 fail closed。
- model output 永不直接 trusted；invalid / unsupported / ambiguous item 不發布。

### E. Trusted learner event model

P06-04 先建立 Assessment submission / deterministic scoring / trusted `AnswerEvent`；這是 downstream Mastery 的唯一 assessment truth source。

最低 AnswerEvent / submission 行為：

- client submission 只帶 StudySession / assessment / question / selected option / idempotency 所需最小 identity。
- learner identity、answer key、correctness、score 全由 server 綁定/推導。
- AnswerEvent 綁 StudySession、material / Map revision、assessment、question、Formal Concept / Claim、selected option、correct/incorrect result、created_at / event identity。
- idempotent / replay-safe / ordered or timestamped / lineage 可追。
- stale assessment、wrong StudySession、cross-learner、cross-material submission fail closed。
- public feedback只在 submit 後投影允許的 correctness / rationale / source資訊；private answer與 generation provenance不洩漏。

若 closed-loop 真實流程需要 `practice_completed`、`review_completed`、`concept_learning_completed` 或 `resource_used` 等 trusted server events，可在對應 Task 以最小 contract加入；**不得為 hypothetical telemetry 預建 event platform**。

V2 不使用 dwell time / engagement duration 作 Mastery / Weakness signal。

### F. Learning State v2

Learning State 是 **StudySession-scoped deterministic derived state**；每個 Concept 至少包含：

- `status`：`not_started` / `learning` / `needs_review` / `mastered`。
- transparent mastery estimate / band。
- confidence。
- Evidence / Claim coverage。
- valid attempts summary、recent / repeated error、post-review trend（若有可信事件）。
- `needs_more_data`。
- student-readable explanation。
- source AnswerEvent / trusted event refs、StudySession ID、base Knowledge Map revision、state revision / watermark。

### Frozen claim-aware Mastery rule

Concept 只有在以下條件**全部**成立時才可 `mastered`：

1. 至少 `>= 2` 個 valid assessment attempts。
2. required Claim coverage 成立。
3. 被納入 coverage 的 Claim，其 **latest valid result 必須 correct**。
4. Concept 有 `>= 2` Claims 時：至少兩個 distinct Claims 的 latest valid result correct。
5. Concept 只有 1 Claim 時：至少兩個不同 item / attempt correct；不得用同一題 replay重複灌高 Mastery。
6. 一次答對永遠不得直接 mastered。

其他規則：

- mastery 與 confidence / coverage 分離；不可用權重包裝成假 psychometric precision。
- no assessment evidence時不得宣稱 mastered；依 explicit learning action最多顯示 `not_started` / `learning`，並標 `needs_more_data`。
- repeated wrong 可形成 needs-review / weakness evidence。
- post-review improvement可反映 trend，但不能抹掉本 StudySession歷史。
- duplicate / invalid / stale / wrong-session event不計入 state。
- 新 StudySession 不讀取上一 StudySession 的 derived mastery。

### G. Weakness model

必須區分：

- **observed weak**：由多次錯誤、低表現或 remediation 後仍錯等可信訊號支持。
- **needs review**：存在近期錯誤或尚不穩定，但未必足以宣稱 persistent weakness。
- **not enough data**：沒有足夠 evidence 判斷。
- **possible prerequisite gap**：目前 target 的 positive-only `prerequisite_constraints` 尚未掌握。

Weakness output 至少包含：

- affected Concept。
- supporting trusted event refs。
- confidence / coverage。
- immediate prerequisite context（如有）。
- remediation intent。
- student-readable reason。

禁止：

- 只因單次錯誤判定 persistent weakness。
- 使用 dwell time判定 weak。
- 用 `related` / `contains` 作 prerequisite gap。
- 使用 prerequisite ancestry / transitive closure；V2只看 immediate prerequisite。
- 使用 raw relation candidates / diagnostics / verifier rejected proposal。
- 沒有可信 learning evidence 時猜弱點。

### H. Learner-specific Adaptive Plan Overlay

Canonical inline `initial_learning_path` 不變；Agent 4 產生 StudySession-scoped learner overlay。**第一版一次只保留 one primary adaptive step，不建立 2–3 step queue。**

Adaptive artifact 至少包含：

- `study_session_id`。
- `base_knowledge_map_revision`。
- base inline `initial_learning_path` identity（由 Knowledge Map revision / content綁定；不得建立 standalone LearningPath artifact）。
- source Learning State revision / event watermark。
- current target / deferred target（若 prerequisite remediation暫緩原 target）。
- one primary adaptive step。
- fallback reason。
- confidence / coverage / supporting state refs。

Routing priority **凍結**：

1. current target 有未掌握 published、positive-only **immediate `prerequisite_constraints`** → `relearn_prerequisite`。
2. 否則 current Concept 有可信 weakness → `review` / `practice`。
3. 否則沿 canonical inline `initial_learning_path` 找 first not-mastered Concept。
4. evidence不足 → `collect_more_data` / `follow_path`，不強行 reorder。
5. all mastered → `no_action` / completion。

Adaptive overlay只能調整此 StudySession的下一步與插入 remediation；不可改 canonical Map / Path，不可建立新 Relation。

### I. Suggestion v2

Suggestion 是 Adaptive Plan 的學生可理解 projection，不是另一套 decision engine。

Frozen action enum：

`start` / `continue` / `practice` / `review` / `relearn_prerequisite` / `use_resource` / `follow_path` / `collect_more_data` / `no_action`

至少包含：

- primary target Concept label / ID。
- action。
- reason。
- confidence / evidence coverage。
- prerequisite context（如有）。
- route IDs / base revisions。
- optional promoted supplementary resource target。
- fallback action / reason。

所有 action 必須能 route 到真實 server / product capability。Suggestion不得重算 Weakness、不得自行修改 routing priority。

### J. Full flow contract / tests

- assessment public/private projection。
- submission / replay / idempotency。
- learner event lineage。
- state revision。
- weakness derivation。
- adaptive plan revision / base map binding。
- suggestion projection。
- UI public view stable，交 Phase 07。
- 同一 session 可完成至少兩輪 assessment / remediation / reassessment，state revision 與 next step 會更新。
- 新 session 不會沿用上一 session 的 mastery / weakness / adaptive plan，除非未來有明確跨 session policy。
- private learning product Golden。

## 主要工作範圍

- P06-01～03 已建立 StudySession / Assessment / canonical Evidence contract；P06-04～10 直接消費 frozen contract，不重新定義。
- 將 `single_choice` evidence-grounded generation + deterministic scoring 鎖為 V2 第一版正式 assessment contract。
- 對 current Map v10 Document Tree 建立 consumer fixture，固定 `prerequisite_constraints` 與獨立 `initial_learning_path` semantics。
- 建立 session-scoped trusted learner-event persistence 與 idempotency。
- 建立 transparent Learning State / confidence / coverage logic。
- 建立 weakness / prerequisite-gap derivation。
- 建立 adaptive plan overlay 與 deterministic next-step policy。
- P06-03 Assessment Generation / validator 已 frozen；後續只做 scoring、state、adaptation、API與 closed-loop wiring。
- 必要 migration / API / public views；若尚無 durable production historical learner data，不為 hypothetical legacy 建 reader/facade。
- P06只提供 stable public fixtures / E2E；final UX 在 Phase 07。
- Golden annotations 與 manual review。

## 已凍結決策與剩餘 implementation choices

### 已凍結，不得在 P06-04～10 重新討論

- production learning session entity = `StudySession`；auth `LearnerSession` 保持獨立。
- Assessment只支援 `single_choice`，exactly 4 options。
- Assessment第一版 Claim / canonical exact Evidence-grounded；不新增 Relation-question contract。
- P06-03 production generation / verifier / repair / new-item architecture與已接受 qualification Gate固定。
- Material runtime identity 與 Assessment runtime identity分離。
- Mastery使用本文件 Frozen claim-aware rule；不沿用 opaque V1 weights、不做 psychometric precision。
- status enum固定 `not_started` / `learning` / `needs_review` / `mastered`；資料不足用 `needs_more_data`，不新增 `unknown` status。
- V2不使用 dwell time / engagement duration。
- prerequisite gap只看 published、positive-only **immediate `prerequisite_constraints`**。
- Adaptive Plan只保留 one primary adaptive step。
- Suggestion action enum固定。
- canonical `initial_learning_path` inline於 Knowledge Map；不建立 standalone LearningPath producer / DB domain。
- StudySession之間不自動聚合 Mastery / Weakness / Adaptive Plan。

### Codex可自行決定的 implementation details

- SQL / transaction / locking / idempotency具體實作，只要符合 frozen contract。
- derived state / confidence / coverage的透明資料結構與 band naming；不得放寬 Mastery Gate或製造假 precision。
- API endpoint命名與 request/response model布局：先讀 current `/v1` API，沿用既有 security / error / idempotency風格。
- explicit practice / review / completion event是否需要獨立 persistence：只依 closed-loop真實需求最小實作。
- `use_resource` 在有 promoted supplementary resource時的 route detail；無 resource 不阻擋 adaptation。
- internal helper / module decomposition / test fixture。

### 需 escalation 的剩餘風險

- 若 P06-08 實際證明目前約 135s cold Assessment lifecycle使 closed-loop無法接受，需要重大 AI residency / worker architecture決策，先交 planner/reviewer；不得自行擴張成新 runtime platform。
- 若真實 integration證明 frozen contracts互相矛盾或無法實作，停止 downstream並回報證據；不得靜默改產品規格。

## 必要限制

- answer key / private scoring fields 永不進 public API / frontend。
- V2 assessment 只允許 `single_choice`；question / distractor 必須 Formal Concept / Claim / canonical Evidence-bound，不可憑模型常識出題。
- data 不足不得標 high-confidence personalized / mastered。
- Agent 4 不改 Map v10 Document Tree、`prerequisite_constraints` 或獨立 `initial_learning_path`。
- hard prerequisite 只來自 positive-only `prerequisite_constraints`。
- `contains` / `related` 不得進入 Agent 4 consumer path。
- raw relation candidate / diagnostics / verifier rejected output 不進 Agent 4 learner decision。
- state 只能由 trusted learner/server-scored events 計算。
- client 不傳 score、mastery、learner ID、suggestion decision。
- historical state / assessment 不 in-place 改語意；若沒有必須保留的 durable data，不建立多餘 legacy compatibility。
- learner state / mastery / weakness / adaptive plan 只在單次 `StudySession` 內有效；V2 不做跨 session 自動聚合。
- 不為尚未存在的帳號系統預先建立複雜 User profile、跨裝置同步或 long-term learner model。
- 不建立 generative tutor chat、free-response grading、IRT 或大規模 adaptive-testing platform。
- reason 對學生可理解；debug codes / source events 只在 details / debug projection。

## 驗證方式

### Knowledge Map consumption

- 只讀正式 published Map v10 Document Tree、獨立 `initial_learning_path` 與 `prerequisite_constraints` artifact。
- `prerequisite_constraints` 與獨立 `initial_learning_path` 行為符合 contract。
- raw candidate / diagnostics 無法進 learner decision path。
- stale Knowledge Map revision 會被偵測，不靜默沿用舊 adaptive plan。

### Assessment domain / security

- `single_choice` 是唯一 V2 supported question type；其他題型必須被 contract 拒絕或視為 unsupported。
- public/private field separation、OpenAPI、DB storage。
- answer uniqueness、option duplicate、Evidence subset、revision / idempotency。
- model / prompt injection content boundary。

### Assessment Golden

- answerability、single correct、distractor plausibility、Concept / Claim / selected Evidence grounding、full-Claim ambiguity risk、language clarity。
- unsupported / hallucinated item zero或 near-zero hard gate。
- 不再依賴舊 `confusing/application/example` Relation 類型。

### Session isolation

- 同一 `StudySession` 內 assessment → remediation → reassessment 可反覆執行。
- session event 不跨 session 聚合。
- 新 StudySession 的 Concept預設 `not_started` / `needs_more_data`；不沿用上一 session mastery / weakness。
- stale / wrong-session item submission 不得污染另一 session state。
- Knowledge Map revision 改變時舊 adaptive plan 不得靜默沿用。

### State logic

- no data。
- one answer。
- mixed answers。
- repeated wrong。
- post-review improvement。
- mastered estimate but low confidence。
- all mastered。
- stale / duplicate / out-of-order events。

### Weakness / prerequisite gap

- observed repeated error。
- insufficient data。
- unmet `prerequisite_constraints`。
- contains / related 不進 consumer path。

### Adaptive plan / Suggestion

- weak current Concept → practice / review。
- unmet prerequisite → relearn prerequisite，再回原 target。
- insufficient data → follow path / collect more data。
- no usable prerequisite → canonical path fallback。
- supplementary resource available / unavailable。
- all mastered → no action。
- target / action / reason / route 一致。
- canonical Map/Path revision 不被修改。

### E2E

- 建立 StudySession → 學習 Concept → Agent 4 產生 evidence-grounded 單選題 → submit / score / replay / conflict。
- state / weakness / adaptive plan / suggestion。
- suggestion action 真正 route 到 Concept / Practice / Resource / Path。
- remediation 後由 Agent 4 產生不同題目或不同 claim coverage 重新作答，state 與下一步可合理更新。
- 同一 session 可重複上述循環直到進入下一 Concept 或 session 結束。
- 開新 session 後不自動繼承上一 session 的 Mastery / Weakness / Adaptive Plan。

## P06-04～10 Long-run Execution / Planner Handoff Policy

P06-00～03 已 frozen。P06-04～10 允許在**同一 Codex App conversation / large Goal**內連續執行，目標是盡可能完成整個剩餘 Phase 06，而不是每個 Task 等人工重新規劃。

每個 Task 固定 checkpoint：

```text
read latest repo / previous frozen contract
→ implement current Task only
→ targeted tests
→ relevant regression
→ git diff self-check
→ signed commit
→ push
→ checkpoint / continue
```

### Codex自行處理，不需 escalation

- SQL / transaction / locking。
- helper / module decomposition。
- migration細節（不破壞已證明存在的 durable data）。
- deterministic state / routing implementation細節。
- test fixture與一般 implementation bug。
- Medium / Low cleanup、命名或效能微調：記錄但不阻擋 long-run。

### 必須透過 Browser交給 ChatGPT planner/reviewer

- frozen contracts互相矛盾。
- current repo證明本文件的 frozen產品規格不可實作。
- 必須修改 P06-01～03 frozen contract才能繼續。
- 必須修改 Agent 3 Relation semantics / Knowledge Map / canonical `initial_learning_path` 才能實作。
- 出現新的 AI/model architecture decision。
- P06-08 latency / residency問題需要 resident worker、queue、常駐 model service或其他重大 runtime architecture。
- tests / review發現 correctness、security、ownership、data-loss、cross-session污染等 Blocker / High issue。

### Review stop rule

- Blocker / High：修正後再繼續。
- Medium / Low：不擋 milestone，留 P06-10 或 V2 functional freeze後 whole-repo cleanup。
- 已通過 acceptance + regression，且 targeted fix review無 Blocker / High → 停止該 Task review，往下一 Task。
- 不因「理論上還能想到更多 edge case」無限追加 Gate；只有真實 failure / integration evidence才重新打開 frozen contract。

### Git / unattended mode

- long-run可使用同一個 Phase 06 execution branch以保留上下文，但**每個 Task 必須獨立 commit / push**；不得壓成 mega-commit。
- unattended期間不得自行 merge `dev`，除非使用者另有明確授權。
- 最終 merge前由 planner/reviewer檢視 task boundaries與 Phase-level regression。
- private教材、Golden、`.studydy-runtime/`、`docs_local/`、`.env`、secrets、model/cache永不提交。

## Codex 執行 Task 拆分

> 本節是 implementation-level execution order。Codex 必須依 Task 邊界小步實作、測試與提交，不得把整個 P06 一次性重寫。除明確標示可並行者外，後一 Task 以前一 Task 的 frozen contract 為前提。
>
> **命名凍結：** `runtime/learner_session.py` / `learner_sessions` 只負責 cookie / auth session；learning-loop production entity 固定使用 `StudySession` / `study_sessions`。不得重新命名、混用或把 auth session 改造成學習進度容器。

### Task 06-00 — Current `dev` Recon 與 P06 Baseline Freeze — **✅ Completed**

已完成 current repo / Knowledge Map / auth-session / storage baseline recon。後續 Task仍需從最新 `dev`讀真實 code，不得只依文件猜 implementation。

Frozen結果：

- integration branch = `dev`。
- Agent 3 canonical Map contract = `knowledge-map/v6`，Relation只有 `prerequisite` / `contains` / `related`，inline `initial_learning_path`；此為 Plan01 前歷史 baseline，已由 Map v10 Document Tree 取代。
- `LearnerSession`保留 auth/cookie責任，不承載學習進度。
- 沒有證據需要為 hypothetical legacy建立 compatibility facade。

### Task 06-01 — StudySession Domain、Ownership 與 Persistence — **✅ Completed / Frozen**

Frozen production contract：

- `StudySession` 與 auth `LearnerSession` 分離。
- server-bound learner / material / exact Knowledge Map revision。
- StudySession lifecycle / current Concept ownership可驗證。
- cross-learner / cross-material / stale Map revision fail closed。
- session-scoped derived state不得跨 StudySession污染。
- 不建立 account profile / cross-session mastery merge。

後續 Task只能 consume / minimally extend此 contract，不重新命名或把 auth session改造成 learning container。

### Task 06-02 — Single-Choice Assessment v2 Contract：Public / Private Projection — **✅ Completed / Frozen**

Frozen production contract：

- 唯一 question type = `single_choice`。
- exactly 4 unique normalized options。
- stable question / assessment / option identities。
- target Formal Concept / Claim / source Evidence binding。
- public/private strict schema；answer key / rationale不進 public。
- answer-independent public identities，不以答案位置作 side-channel。
- immutable replay / conflict semantics。
- Claim-grounded第一版；不新增 Relation-question refs。

P06-04 scoring / feedback只能從 private server storage取得 answer，不修改 P06-02 public contract。

### Task 06-03 — Evidence-Grounded Question Generation + Qualification Gate — **✅ Completed / Frozen**

Accepted candidate baseline：`161cf9f6761e187ba944b9b71b96ddea454f09df`（開始 P06-04前需已合回最新 `dev`）。

Frozen結果：

- canonical exact Evidence由 `study-material-output/v5` durable handoff；Agent 4不重新 OCR / reinterpret PDF。
- Qwen3-14B產生3個 bounded grounded proposals。
- deterministic structural / duplicate / leakage gates。
- selected-Evidence relative NLI margin `>= 0.1`證明正式引用 Evidence真的支持正解。
- full-Claim relative NLI margin `>= 0.1`；maximum distractor `>= 0.4`觸發 multiple-support repair。
- repair：3 candidates、每 candidate 5 mutation proposals、deterministic mutation proof、retain safe distractors；repair失敗不回退 risky proposal。
- verifier `truncation=False`；完整 Evidence-option pair >384 tokens即 inference前 fail closed。
- deterministic new-item selection會跳過同 StudySession / Claim已使用 question；repair pool耗盡後繼續較低排名 safe proposals。
- private generation provenance / Assessment runtime binding獨立於 Material runtime identity。
- qualification baseline：Representative 24/30，critical 0；holdout critical 0；multiple-supported unsafe 0；P06-02 contract 29/29；selected Evidence grounding 29/29；stability 14/14；public leakage 0。

後續不得重新開啟 P06-03 model architecture research；除非 P06-08/09真實 integration evidence證明此 frozen contract不可用。

### Task 06-04 — Submission、Server-Side Scoring 與 Trusted AnswerEvent

**依賴：** 06-03 frozen contract；P06-03 必須已合回最新 `dev`

**目的**

把使用者選項轉成可信 learning signal。

**工作**

- submission request 只收 StudySession / assessment / question / selected option / idempotency所需最小必要 identity；具體 endpoint payload依 current API風格決定。
- learner identity 從現有 auth session server-side 解析。
- answer key 只從 private server storage 讀取。
- deterministic correct/incorrect scoring。
- AnswerEvent 綁定 StudySession、material / Map revision、assessment、question、Concept / Claim refs、selected option、deterministic correct/incorrect result、created_at / event identity。
- replay / duplicate / stale assessment revision / cross-session submission fail closed。
- feedback public projection 在 submit 後才返回允許的 correctness / rationale / source projection。

**禁止**

- client 傳 `score` / correctness / answer key / learner ID。
- 用模型重新判 single-choice 是否正確。

**完成條件**

- submission idempotent。
- private/public separation tests 通過。
- AnswerEvent 可作 downstream state 的唯一 assessment truth source。

---

### Task 06-05 — StudySession-Scoped Learning State v2

**依賴：** 06-04

**目的**

只由 StudySession 內 trusted events deterministic 推導 Concept state，明確分離 mastery、confidence 與 coverage。

**最低輸出**

- `status`: `not_started` / `learning` / `needs_review` / `mastered`。
- transparent mastery estimate / band。
- confidence / evidence coverage / Claim coverage。
- attempts、recent / repeated errors、post-review trend（若有可信 remediation events）。
- `needs_more_data`。
- source event watermark / state revision / base Map revision。

**Frozen Mastery Gate**

- `>= 2` valid assessment attempts。
- required Claim coverage成立。
- 被納入 coverage 的 Claim，其 latest valid result必須 correct。
- Concept有 `>= 2` Claims：至少兩個 distinct Claims latest valid result correct。
- Concept只有1個 Claim：至少兩個不同 item / attempt correct。
- 一次答對永遠不得直接 mastered。
- replay / duplicate / invalid / stale / wrong-session event不算 valid evidence。

**其他規則**

- mastery與confidence / coverage分離，不做IRT / opaque V1 weighting。
- no assessment evidence不得 mastered；只可 `not_started` / `learning` + `needs_more_data`。
- repeated wrong形成 needs-review / weakness evidence。
- V2不使用 dwell time。
- StudySession B不得讀取 A 的 derived mastery。

**測試矩陣**

- no data。
- one correct / one wrong。
- mixed answers。
- repeated wrong。
- repeated correct but insufficient Claim coverage。
- multi-Claim latest-result correctness。
- single-Claim two-distinct-item requirement。
- post-review improvement。
- duplicate / out-of-order / stale / wrong-session events。
- all mastered。

**完成條件**

- 同一 valid event stream deterministic產生同一 state。
- low data不過度判定。
- frozen Mastery Gate逐項有 tests。

### Task 06-06 — Weakness 與 Published-Prerequisite Gap Derivation

**依賴：** 06-05

**目的**

把「答不好」拆成 observed weak / needs review / not enough data / possible immediate prerequisite gap。

**Frozen規則**

- observed weakness只能由 trusted assessment / practice / review evidence支持。
- prerequisite gap只能讀 positive-only `prerequisite_constraints`。
- 不做 prerequisite ancestry / recursive closure。
- `contains` / `related` 永不進 Agent 4 consumer path。
- raw relation candidate、diagnostics、rejected verifier output不進 learner decision。
- no data不猜 weakness。

**輸出**

- target Concept。
- weakness category。
- supporting event refs。
- confidence / coverage。
- prerequisite constraint context（若有）。
- remediation intent；完整 routing留給06-07。

**完成條件**

- contains / related誤判 prerequisite的negative tests存在且通過。
- cycle edge不形成 remediation dependency。
- insufficient data與observed weakness明確分離。

### Task 06-07 — Adaptive Plan Overlay + Suggestion Projection

**依賴：** 06-06

**目的**

完成 Agent 4 deterministic learner adaptation；不改 Agent 3 canonical Map / inline path，只產生 StudySession-scoped overlay。

**Routing priority 固定**

1. current target有未掌握 published non-cycle immediate prerequisite → `relearn_prerequisite`。
2. 否則 current Concept有可信 weakness → `review` / `practice`。
3. 否則沿 canonical inline `initial_learning_path` 找 first not-mastered Concept。
4. evidence不足 → `collect_more_data` / `follow_path`。
5. all mastered → `no_action`。

**Adaptive artifact**

- StudySession ID。
- base Knowledge Map revision / inline path identity。
- source Learning State revision / event watermark。
- current / deferred target。
- **one primary adaptive step only**。
- fallback reason / confidence / coverage。

**Suggestion projection**

Frozen actions：`start` / `continue` / `practice` / `review` / `relearn_prerequisite` / `use_resource` / `follow_path` / `collect_more_data` / `no_action`。

Suggestion只投影 Adaptive Plan：target / action / reason / confidence / route；不得重算 decision。

**完成條件**

- stale base revision可偵測。
- canonical Map / inline path bytes / revision不被修改。
- remediation後可回 deferred target。
- no resource不阻擋 adaptation。

### Task 06-08 — P06 Public API Surface 與 End-to-End Contract Wiring

**依賴：** 06-01～06-07

**目的**

把 frozen domain capability依 current `/v1` API / security / idempotency風格正式發布；不得另建平行 app。

**必須具備的 public capability**

- create/read/complete StudySession。
- read current Concept / canonical inline path context。
- request/read single-choice Assessment。
- submit answer / receive safe feedback。
- read Learning State / Weakness。
- read Adaptive Plan / primary Suggestion。
- continue practice / reassessment需要的最小 action。

**API rules**

- 沿用 cookie learner identity / Origin / idempotency / fixed safe error convention。
- strict closed public models。
- no answer key / private rationale-before-submit / raw model output / NLI scores / DB path / internal diagnostics leakage。
- 所有 learner-owned resource驗證 learner + material + StudySession ownership。
- P07只靠 public API即可完成，不允許 DB / fixture shortcut。

**Known runtime issue / escalation boundary**

目前 direct cold Assessment generation約135秒。P06-08先完成 correctness / API wiring；**不得只為 latency自行建立 resident worker、queue、常駐 model service或新的 AI runtime architecture**。

若真實 API / P06-09 flow證明 latency使 acceptance無法成立，使用 Browser將 evidence交 planner/reviewer，取得 architecture decision後再調整。

**完成條件**

- OpenAPI contract tests。
- current material-review endpoints regression zero。
- private/public boundary完整。
- frontend可依 public API進 P07。

### Task 06-09 — Closed-Loop Integration / Golden / Regression

**依賴：** 06-08

**目的**

證明 P06 不是「有幾個 endpoint」，而是真的能在同一 StudySession 反覆學習。

**必跑 E2E case**

```text
start StudySession
→ learn target Concept
→ generate single-choice assessment
→ answer wrong / repeated wrong
→ state becomes needs_review / weakness
→ detect unmet prerequisite 或 current weakness
→ adaptive action routes to remediation
→ complete review / practice
→ generate different assessment item(s)
→ answer again
→ state/confidence/coverage update
→ return deferred target 或 advance to next Concept
```

另驗：

- low-data fallback。
- no-resource fallback。
- all-mastered completion。
- StudySession A/B isolation。
- answer leak negative test。
- stale map / assessment / state revision。
- duplicate / replay / out-of-order event。

**Golden**

- 題目品質 + evidence binding。
- state derivation。
- weakness/prerequisite correctness。
- adaptive routing correctness。

**完成條件**

- P06 Completion Gate 全部可由 automated + private Golden evidence 支持。

---

### Task 06-10 — P06 Cleanup、Contract Freeze 與 P07 Handoff

**依賴：** 06-09

**目的**

只做 Phase 06 收斂，不加功能、不做 whole-repo architecture refactor。

**工作**

- 移除 **Phase 06範圍內** 已確認無 consumer的 dead code / temporary scaffolding。
- 不為 obsolete local development data建立 compatibility / facade / re-export。
- freeze schema / OpenAPI / fixtures / Golden revisions。
- 公開 P07所需 fixtures：success / low-data / weakness / prerequisite-gap / reassessment / completed / stale / failure。
- 記錄 known limitations：single-choice only、StudySession-scoped、no cross-session learner model、Assessment cold latency（若尚未解決）。
- 全 backend tests / migration fresh install / full-stack harness / relevant local runtime verification。

**禁止**

- 不把 P06-10擴張成 Agent 1～4 whole-repo simplification。
- 不做全域 abstraction cleanup / naming sweep / framework rewrite。
- V2 functional freeze後的 whole-repo simplification另開獨立 Goal / branch處理。

**完成條件**

- P07不需要要求 P06 major contract redesign。
- Phase 06 branch clean，無 secrets / `docs_local/` / private material / Golden。
- known limitations與 P07 / P08 handoff清楚。

### P06 Long-run Branch / Commit 策略

P06-01～03 已完成。剩餘 P06-04～10 可作為一個「完成 Agent 4 closed loop」large Goal，在同一 Codex App conversation與 execution branch長跑，以避免人工 context switching。

要求：

- branch 必須從 **P06-03已合併後的最新 `dev`** 建立。
- 06-04、05、06、07、08、09、10 各自形成清楚的 signed commit / checkpoint；必要 targeted fix另加小 commit。
- 每個 checkpoint push remote，方便 Browser / GitHub reviewer讀取。
- 不把 04～10 squash成 mega-commit。
- unattended期間不得自行 merge `dev`（除非使用者明確授權）。
- planner/reviewer只以 Blocker / High阻擋 long-run；Medium / Low留最後 cleanup。
- 最終可用一個 Phase-level PR回 `dev`，其單一目的為「完成剩餘 P06 Agent 4 closed loop」；若 reviewer認為 diff過大，再依 task boundary拆 PR，不需預先為形式而阻斷長跑。
- 不跨 Task預埋未使用 facade / re-export / compatibility shim。

## Completion Gate

- Agent 4明確是 learner adaptation capability，不是另一個 Map / Relation generator。
- StudySession ownership / isolation成立；新 StudySession不自動帶入舊 Mastery / Weakness / Adaptive Plan。
- P06-03 frozen Assessment safety不退步：Claim / selected Evidence grounded、exactly 4 options、answer key private、critical unsafe promotion不得出現。
- submission由 server deterministic scoring；client不能提供 score / correctness / learner identity。
- AnswerEvent成為 downstream assessment truth source，idempotent / lineage / session binding成立。
- Mastery符合 frozen claim-aware rule；confidence / coverage與 mastery分離。
- observed weak / needs review / not enough data / immediate prerequisite gap可區分。
- prerequisite gap只由 positive-only `prerequisite_constraints` 推導；contains / related不進 Agent 4 consumer path。
- learner-specific Adaptive Plan以 one primary step在不修改 canonical Map / inline path下調整下一步。
- Suggestion只做 Adaptive Plan projection，提供 frozen action / target / reason / route。
- data不足退回 `collect_more_data` / `follow_path` / canonical path，不強行 personalize。
- 同一 StudySession至少完成兩輪 assessment → remediation → new-item reassessment，state revision與下一步合理更新。
- public API / OpenAPI足以讓 P07不碰 DB / private fields。
- P06-09 closed-loop automated + private Golden evidence成立。
- P06-10只做 Phase 06收斂；whole-repo simplification延後。
- stable public contract交 Phase 07；security / Golden / release-hardening evidence交 Phase 08。

## 可降級 / Fallback

- Assessment無安全候選 / Evidence不足 / verifier超限：不出題，回 coverage limitation / collect more data；**不新增 unsafe fallback、不降低 P06-03 Gate**。
- Mastery evidence不足：保持 `needs_more_data` / non-mastered，不放寬 frozen Mastery rule。
- prerequisite不足：follow canonical inline initial path，不自行推論新 dependency。
- learner data少：`collect_more_data` / `follow_path`，不強行 adaptive reorder。
- Resource無 promotion：指向 Concept / Evidence / Practice，不阻擋 adaptation。
- V2不使用 telemetry duration；不因缺少時間訊號阻擋 state derivation。
- 若 adaptive routing資料不足：輸出單一 conservative primary step + reason。
- 若 135s cold lifecycle阻擋 P06-09：先 escalation做 runtime architecture decision，不在 P06-08偷偷造平台。

## 明確不在此階段處理

- multiple-select、true/false、short-answer、essay、coding-response 等非 `single_choice` 題型。
- free-response LLM grading。
- IRT / psychometric adaptive testing。
- chat tutor。
- gamification / social leaderboard。
- long-term spaced repetition scheduler / retention model / 遺忘曲線。
- 跨 `StudySession` 自動 Mastery 聚合與永久 learner model。
- 帳號 / profile / 多裝置同步 / 長期學習歷史整合；需要時在後續 Phase 另行設計。
- 重建 Knowledge Map / Relation taxonomy。
- final visual polish。

## Handoff

交給 Phase 07：

- `StudySession` public lifecycle / current-session state contract。
- `single_choice` Assessment public view / exactly-4-option item / progress / feedback / reassessment states。
- Learning State：status + mastery estimate + confidence / coverage。
- Weakness explanation + prerequisite gap。
- canonical initial path + learner adaptive overlay 的呈現 contract。
- Suggestion action / target / reason / route IDs。
- all empty / partial / insufficient / stale / error fixtures。

交給 Phase 08：

- learning product Golden。
- security / idempotency / revision tests。
- Demo assessment set。
- 至少一條「學習 → 單選題 → 答錯 → 弱點 → prerequisite/remediation → 新題再測 → state / 下一步更新 → 繼續學習」真實 full-flow case。
- session isolation case：新 session 不自動沿用上一 session 的 Mastery / Weakness / Adaptive Plan。

# 觀念圖卡（F4）

## Phase A

基線 dev：`3e3bfcfcf2616d86fca3d687a228b532262990f5`。
相較規劃基準 `76dfa532`，新增的是部署／HTTPS 設定，KS authority 不變。

`F4_BASIC_PHASE_A_PASS`

- `backend/tests/test_concept_cards.py`：6 passed。合成資料沿用正式 KS builder／validator，涵蓋多 claims、prerequisite、contrast、無關概念、程式碼／公式、needs_review 與排除頁。
- 每個 claim 與 relation 引用仍指向 canonical Evidence；方向、literal 與品質逐項斷言，不使用畫面 snapshot 代替。
- 本機 Chromium 最小樣張於 1440px／390px 驗證文字 reflow、程式碼局部水平捲動、完整文字不變。這是版面可行性證據，完整產品互動另驗。
- 現有發布驗證要求 relation evidence 屬於端點概念；可直接使用既有 Evidence source resolver。
- API 教材 UUID 與 KS 內容 material hash 是不同 identity，selection 必須同時保留。

基本模板限定單卡／多卡並列及既有關係文字，不生成定義、比較軸或摘要。
自動選材的排序／數量尚無核定規則，manual slice 不依賴此決策。

## 使用方式與契約

知識地圖 →「選取觀念圖卡」→ 勾選一個或多個概念 →「開啟圖卡」。
卡片重點、Evidence 節錄與來源位置可展開，來源沿用既有 resolver／PDF 與原檔入口。
關閉 modal 保留原地圖路由及瀏覽狀態，焦點返回入口；選取只存在當次 modal，不保存或收藏。

`concept-cards/v1` 的 `selection` 包含：

| 欄位 | 意義 |
| --- | --- |
| `material_id` | API 教材 UUID，由 server 驗證擁有權 |
| `content_material_id` | canonical KS 的教材內容雜湊身分 |
| `knowledge_structure_revision` | exact published KS revision |
| `concept_ids` | 所選概念，依 canonical concepts 順序，輸入重複 ID 合併 |
| `claim_ids` | 所選概念的全部 claims，依首次出現順序去重 |
| `relation_ids` | 與所選概念相接的既有 relations，維持 canonical 順序 |
| `policy` | `manual-published-order/v1`：手動選材、發布順序 |

representation 與 renderer 排版分離；回傳 cards、relations、status、excluded_pages 與固定版本 source_resolver。
relation 保留原方向、type 與 learner_reason；未選取的端點標明為外部概念。
Claim 文字與引用節錄原樣保留；relation 的 Evidence 使用 canonical 完整區塊原文，不任取某條 claim 的節錄。
無所選概念間關係時只並列已有重點，沒有生成定義、比較軸、優缺點或新知識。

## 唯讀 API

`GET /v1/materials/{material_id}/knowledge-structures/{revision}/concept-cards?concept_id=...&concept_id=...`

既有 KS API 只提供整張圖，且 public `material_id` 是內容身分。開卡需重新確認 revision 可用、由 server 驗證 selected IDs，並取得 relation 完整 Evidence；因此增加此最小聚合入口，避免重新傳整張 KS 或在前端重做投影／驗證。
入口重用 `read_knowledge_structure` 的 owner、material、run、revision、source binding 驗證，不新增資料表、儲存、migration、job 或模型服務。

- 未登入：401；錯 owner／material／不可用或損壞 revision：404。
- 空選取、未知 concept ID、未知 query：400；合法重複 concept ID 去重。
- 有保留的舊版可讀，不會默默切到 head。沒有持久 retention；已清除的 revision 明確失敗，需重新選取。
- source 展開仍走 exact revision 的既有 resolver；缺失／模糊來源保留錯誤或原有定位警示。
- App 的帳號 client invalidation 與路由 key 隔離私有資料；modal 關閉／卸載使晚到請求失效。
- 不建立 StudySession、不寫 mastery／answer event，也不回寫 KS。

## Phase B 驗證

最終驗證使用合成 fixture、隔離 PostgreSQL、正式 production frontend build；沒有連接產品 DB 或呼叫 AI／Pod。

| 驗證 | 實際結果 |
| --- | --- |
| `backend/tests/test_concept_cards.py` | **8 passed**：identity、穩定順序、literal、完整 relation Evidence、方向、無補比較、共享 claim 去重、品質、dangling refs；含 API response model 序列化一致性 |
| `backend/tests/runtime/materials/test_concept_cards_api.py` | **3 passed**：owner、錯教材、exact／不可用 revision、無效 ID、來源 resolver、損壞 canonical Evidence 拒絕、無 session／有 progress 皆唯讀 |
| `backend/tests/runtime/materials/test_concept_cards_browser.py` | **1 passed**，內含 **1 real-API Playwright case**：登入、single／multi、canonical 對照、exact source 與真 PDF bytes |
| `npm --prefix frontend test` | **6 test files passed**，包含新增 client identity／provenance／invalidation 案例 |
| `npm --prefix frontend run typecheck`、production build | **passed** |
| `concept-cards.spec.ts`＋既有 `knowledge-map-details`／`knowledge-map-viewport` mock browser | **32 passed**（F4 10、既有地圖 22）；1440／390px、鍵盤、選取、展開、關係、來源失敗、缺理由、品質、長內容、換頁／帳號／晚到回應 |
| `git diff --check` | **passed** |

端到端測試封鎖 backend 模型 HTTP，觀察所有非 session API 寫入為零。
看卡前後 `product_snapshot` 對照 materials、artifacts、processing runs、KS、StudySession、assessments、answer_events 的完整 row 摘要一致；existing LearnerProgress 也逐值相同。
瀏覽器 assertions 驗證文字原文與 source URL identity；截圖僅用於人工檢查版面，沒有用 snapshot 代替 provenance。

重跑入口（沿用 `docs/testing.md` 的隔離環境）：

```bash
env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
  PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests/test_concept_cards.py \
    backend/tests/runtime/materials/test_concept_cards_api.py

env -u STUDYDY_TEST_POSTGRES_DSN -u STUDYDY_DATABASE_DSN \
STUDYDY_E2E_FRONTEND_DIST="$PWD/.studydy-runtime/test-frontend" \
STUDYDY_E2E_FRONTEND_PORT=4183 STUDYDY_E2E_API_PORT=8002 \
PYTHONPATH=backend/src:backend/tests:local_ai/src \
  backend/.venv/bin/pytest -q backend/tests/runtime/materials/test_concept_cards_browser.py
```

建置有非阻擋性的 Vite bundle-size 提示；TestClient 有既有 httpx 棄用警告。未進行真實手機／Safari 驗收，不把 Chromium 390px emulation 宣稱為實機測試。

## 延後項目與最小產品決策

自動快速複習尚需核定：選材依「弱點／目前概念／完整 learning path」何者優先，以及每次數量或是否全選。既有 progress 與 path 提供資料，但沒有 F4 的排序／數量政策；本輪不自行發明。

收藏／歷史保存、跨教材、複雜比較模板、匯出、AI 摘要及 F1／F5 實作均延後。
Basic deterministic F4 的 AI Phase C 不適用。沒有部署 production，也沒有修改競賽 checkout 或 checkout 外的規劃文件。

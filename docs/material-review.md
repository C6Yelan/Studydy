# 教材分析結果的檢核與整理

提供共用檢核模組與正式 worker 消費者，使用已保存的來源及分析結果。
使用 runtime lock 指定的 HTTP 語意服務，與教材分析共用模型設定與完整性驗證。
runtime lock v1 的 `material_review` 設定啟用發布前檢核。新教材及追加來源在初始分析後執行；
已發布教材可以只重整，不重跑 OCR 或初始分析。舊工作保存的設定與資料 hash 不改寫。

## 正式產品流程

worker 依來源與連續頁段建立批次，同一頁的分類／表格不拆開，跨頁概念只分派一次。
每批目標 40 個概念；這是請求分批大小，不限制整份教材節點數。每個批次帶完整頁段 Evidence，
包含未成為 Claim 的原文，以免檢核只能看見初始分析挑出的片段。跨批次的整合目前不提出。

模型回應通過來源、覆蓋、先備及字面值檢查後，由程式建立 canonical Claims、Concepts、Relations
及完整學習路徑，重新計算實際改變內容的 ID 與 revision。成為重點或案例的 Claims 納入所屬觀念，
保留全部原始 Evidence；原節點、修正前 Claims、背景項目及內部關係保存在私人 analysis archive。
有疑點仍發布為 `partial/needs_review`，不把結構檢查當成內容驗收。

既有教材的已登入呼叫入口：

```text
POST /v1/materials/{material_id}/review
Idempotency-Key: <unique-key>
{"schema":"material-review-create/v1","base_revision":"<current-revision>"}
```

入口排入同一個串行 worker，使用既有來源集合及明確 base revision。程式以「來源集合與 base 完全相同」
辨識只檢核工作，不新增 DB schema 或另造執行器。重播同 key 回傳同工作；過期 head、併發工作仍拒絕。
發布時建立新 run／KS 並切換 head，保留原 KS 與 StudySession，不移轉或重算舊答案。
目前提供 API 入口；教材庫的新教材／追加流程自動執行，尚無獨立重整按鈕。

每批一次初始呼叫。若概念覆蓋或概念來源歸屬不合法，帶著原輸入、失敗提案與具體問題補正最多一次；
仍須通過完整驗證，不以刪除重複資料、重新編號或放寬來源規則處理。原始與補正回應分別保存。
已保存的同類失敗回應可直接進入補正，不重跑初始呼叫。其他錯誤仍停止，不無限重試。
失敗不切換 head；失敗後重試或明確取消後重新檢核，
可重用輸入與設定一致的已保存回應。
私人 `analysis/<owner>/<material>/<run>/review/` 保存來源、請求、原始回應、快取及結果對照。
KS `semantic_calls` 計入消費的回應（含明確重試沿用），`review/result.json` 的 `model_calls`
另記本次真正新增的檢核呼叫，不能把重用回應說成本次費用。

## 整理責任

`knowledge_map/material_review.py` 以同一來源的完整頁段建立單元，保留跨頁／跨來源 Claim 的完整引用。
Concept、Claim、Evidence、Relation 都由程式建立短整數索引；模型不產生 hash 或改寫原文引文。
分別提出保留、整合為重點、歸入案例、背景資訊與待確認，以及別名移除、文字補正及關係修正。

模型的歸屬選擇必須指向已存在的單元內節點。程式從父子兩端的既有 Claims 建立來源綁定，
另存模型實際引用的 Evidence，不把程式補齊的綁定冒稱模型引用。未知 ID、缺項、循環與來源變動會失敗。

章節主題或先備關係端點不會被當背景直接排除。
若模型把父概念繼續併到更大的主題，程式先保留父概念，
僅套用可解釋的下層歸屬，記錄被阻擋的修改；不把整章壓成一個學習點。
既有必要先備關係的端點也保留獨立觀念，避免整合後消失或改變必要學習順序。這是保守保護，可能留下片段待審。

文字修正沿用原有字面值檢查，補查中文緊接的數字。僅對字面值不變的中文英文括注（如 PDCA）容許散文整理；
不放寬 code/formula、數值、識別詞或運算子的保護；另保護 `↑↓←→≤≥` 等方向及條件符號，
避免將帶方向的圖表文字改寫成泛稱。無法支持的修正保留原 Claim，不能以 fallback 當成功。
若修正加入本單元另一個已知概念名稱，卻沒有引用包含該名稱的來源，也會阻擋；這可攔下把表格項目錯掛到另一個主體的已觀察錯誤，但不是完整的語意正確性證明。

來源中的 Claims、案例與 Evidence 全部保留；移出獨立學習點不等於刪除資訊。
關係跟隨歸屬形成單元內或跨單元關係，原關係 ID 作為來源追蹤，並檢查先備循環與學習路徑覆蓋。
移除錯掛的關係時，可引用端點同一來源、同頁的章節標題作為脈絡，但仍須至少一個端點引用。
反轉或移除可重述原型別；若同時指定不同型別則拒絕，型別更正必須明確使用 retype。
例子收進單元後，原方向或型別有疑慮仍會留下提示，不能藉由不畫邊而掩蓋錯誤。

整理投影為 `material-review-projection/v1`，不是正式 KS，`publication_authorized=false`、`status=needs_review`。
結構檢查通過不代表語意或教學品質通過；本模組不自行搬移舊作答到新觀念，也不重算掌握狀態。

`combine_reviews` 可彙整互不重疊的小節提案，重新檢查整張圖的來源完整性與學習順序。
重疊小節及互相衝突的修正均須先處理，不採最後一份覆蓋前一份。

## 驗證

```bash
PYTHONPATH=backend/src:backend/tests:local_ai/src backend/.venv/bin/pytest -q \
  backend/tests/test_material_review.py backend/tests/test_semantic_service_v1.py \
  backend/tests/test_semantic_service_v1.py backend/tests/test_knowledge_structure_v1.py
```

合成測試覆蓋歸屬與完整性、案例保存、核心保護、數值／程式保護、來源身分、先備循環與共用 transport。
真實模型的原始結果與助手原文審查另保存在私人實驗目錄，不加入 Git，也不冒稱人工 gold。

`runtime/test_material_review_flow.py` 使用隔離 PostgreSQL 驗證正式 worker 的重整發布、零 OCR／初始分析、
原版可讀、重播／過期版本、追加時自動檢核，以及失敗後只補未完成批次。模型使用受控回應。

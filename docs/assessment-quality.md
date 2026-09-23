# 題目品質與可用性（B5-Q）

依 2026-09-20 使用者要求，品質改良採「安全候選排序」，不設教學品質最低分。目前由 B05 題組使用三個候選、一次 blind solve、後端評分與學習政策；舊單題 API 已移除；本文記錄 B5-Q；已完成的 B5-D 題組見 [assessment-sets.md](assessment-sets.md)，B5-R 補強見 [assessment-remediation.md](assessment-remediation.md)。下列各輪驗證與切換依當時狀態記錄。

## 正確性與品質分開

正解仍須是指定 Evidence 的原文 span，四個選項正規化後不同；另一個未取得私有答案的模型呼叫確認唯一可答，結果必須與私有正解一致。錯誤答案、多解、無解、來源不符與已知同事實重複仍拒絕。Structured Output 只驗格式，不能代替內容正確性。

同一次 checker 呼叫另外回報五種品質提示：無意義的題材、答案線索、措辭不清、選項型態不一致、干擾項偏弱。後端只在安全候選中比較：先避開無意義題材，再避開答案線索，最後比較提示數量；平手保留原順序。所有安全候選都有提示時，仍發布其中相對較好的題目，不追加模型呼叫追求完美，也不因品質提示降低該題既有的作答資格。重複列出的同一品質提示只算一次。

基礎定義、API 名稱、規則與直接回憶題皆可採用。干擾選項出現在教材其他位置不代表它能回答本題；相同答案詞也不代表兩題測的是同一件事。三個候選是擇一發布的替代題，不要求教材必須包含三個不同事實。

前置 `partial`、`needs_review` 或 `source_review_required` 不直接阻擋出題；依各題實際引用的原文判斷。Claim 只提供目標脈絡，不能提供原文不存在的條件。來源缺損與前置理解錯誤仍可能讓題目不可用，本改動不宣稱修復那些內容。無安全新題不產生 AnswerEvent、不代表學生不會；前端明示未出題不算答錯。

## 重複檢查與歷史

從同一 session／exact KS 的歷史題目挑選有限比較集合：同 Claim、Evidence 重疊、同 Concept、其餘近期題目依序優先。最多讀取 32 筆候選，完整納入最多 8 題及 12,000 UTF-8 bytes 的比較內容；超出時略過整題，不截斷題幹或來源。這是模型比較上下文的安排，不是教材容量或使用者初篩題數上限。

generator 與 checker 使用相同集合，checker 只接收歷史題目及選項，不接收任何私有正解。發布紀錄保存實際比較過的 Assessment revisions，不宣稱全歷史語意去重。同 session 的 exact normalized identity 另查完整已保存集合，即使該題未放入 prompt 也不重複發布。

## 版本與資料保存

現行 runtime lock、generator request、checker request／response、policy `source-span-single-choice/v1` 與 provenance 均為 v1；沿用各自最新資料形狀與 prompt。model、OCR、material semantics、context 與 generation budgets 保持原設定。語意模型由 runtime lock 指定並透過 HTTP 呼叫。

新 provenance 私存選中候選、檢查／安全候選數、品質提示、比較範圍及兩個 prompt hashes；模型身分與 runtime lock hash 一併保存。公開題目、resume、history 不加入這些欄位或私有答案。

歷史紀錄不補欄位、不改 hash 或 mastery eligibility；現行 reader 僅接受 provenance v1，不提供舊版相容分支。出題功能本身沿用既有資料模型；教材接續所需的非機密設定由現行 `0002_sources_and_processing.sql` 保存。後續 B5-D 已將題組模型呼叫移至短交易之外，沿用本頁候選準備與檢查流程。

教材與出題設定已解耦：所有教材工作使用同一條執行流程，比對實際影響教材分析的 Python／套件、OCR、semantic service與 material semantics；不以 assessment 或整份設定的版號決定能否接續。工作保存開始時的設定並核對其原 runtime binding，執行與發布仍使用該份設定，因此不改寫舊 run、KS 或 checkpoint 的 hash。新重試可以重用分析設定相同的已保存進度；設定缺失、損毀或教材依賴改變時停止並明示，不能暗中重新分析。沒有新增 legacy 執行分支或按歷史版本切換的 fallback。

2026-09-20 後續已修正 [checkpoint 生命週期](source-revisions.md#分批保存與失敗重試)：地圖發布成功即清理，不受 `needs_review` 影響。已完成教材不需要 checkpoint 接續，也不因品質提示阻擋服務切換。

## 驗證範圍

`test_assessment_safety_v1.py` 驗證安全優先、品質排序、所有候選皆有品質提示仍可發布，以及合法基礎題與相同答案不同問題。`runtime/test_assessment_quality.py` 用真 PostgreSQL 與受控語意 fixture 驗證部分來源仍可出題、跨 Claim 重複不產生錯答、比較集合限界與現行 provenance v1 讀取；閉環測試保留評分、重播與恢復契約。

這些測試證明程式行為，不證明真實模型品質。替代模型舊新比較與正式 Gemma 品質需分別記錄；未執行的檢查不算通過。

### 本次開發驗證（2026-09-20）

結果對應 `main/dev` 的未提交 B5-Q 工作樹；沒有變更競賽版、產品 DB 或日常服務。

- 非 runtime 後端及 local_ai：150 passed。最後新增的品質提示去重案例另包含於下列回歸。
- runtime 與 assessment safety：首次 191 passed／2 failed；一項為轉檔 browser 使用了已占用的產品 port，另一項為既有 worker 單元測試未隔離新增的 analysis 清理呼叫。改用 4183／8002 測試 ports、補齊該 fixture stub 後，兩個失敗案例均通過；沒有修改 worker 產品行為或重跑已通過的整套測試。
- 題目恢復的真 API／DB browser：1 passed，含原題與回饋保存、回應遺失後查回及 GET 不呼叫模型。
- 前端 Node tests：5 passed；TypeScript／production build 通過，保留既有超過 500 kB chunk 提示。
- 靜態核對：OCR、material semantics、model/server pins、generation budgets 與 public/private 題目契約均未變動。

使用者授權三組自製合成短文，以私人設定的 `gpt-5.6-luna` 比較舊新出題政策；共 12／12 次真實呼叫，無重試、無 Pod、無私人教材或 OCR。每組每政策各一次 generation 與一次 blind check。

| 比較 | 舊 policy v5 | 新 policy v6 |
|---|---:|---:|
| 成功選出可用題目／測試組數 | 3／3 | 3／3 |
| 通過本機來源／選項檢查的候選 | 9／9 | 9／9 |
| 模型呼叫 | 6 | 6 |
| 三組累計 generation＋check 等待 | 84.311 秒 | 82.720 秒 |

檢查全部 18 個候選與六道選中題目後，在此範圍未觀察到選中題的錯誤正解或多解；六份新舊題目文件亦通過現行 stored reader 與私有欄位隔離檢查。這是 assistant 來源審查，不是人工 gold。模型比較未將題目寫入產品 DB；DB／API／browser 契約由上述受控測試另外驗證。

簡短定義在新流程仍可出題；字元題候選的選項由混合字元／數量／型態改善為同類字元。條件題的選中候選有可比較的符號數量選項，但未選中候選仍有型態不一致、checker 卻回空品質提示的情況。品質判斷與語意重複偵測仍有侷限，不能把「未提示」當作完美題目。

本次可記 **DEV_FLOW_PASS（上述合成短文範圍）**。每組只有一次舊新樣本，不宣稱一般教材品質、穩定出題率或速度提升；尚未重跑使用者教材，正式 Gemma 品質 **NOT_RUN**。CLI 沒有提供可解析的 structured token usage，未填造 token 數。比較輸入、policy snapshots、12 次原始呼叫及審查結果留在工作區私人 `../.studydy-product/experiments/b05-quality-20260920/`，不加入 Git。

### 收尾與日常服務切換（2026-09-20）

教材設定解耦、checkpoint 清理與 B5-Q 已一起載入日常服務，入口仍為 `http://127.0.0.1:4173`。切換前確認沒有進行中的教材／轉檔工作，備份 DB 並套用 migration 0010；以原 runtime binding hash 核對後，為四個既有工作補入非機密設定。原有欄位與 migration checksums 保持不變，沒有重寫 KS、題目、答案或 checkpoint hashes。

驗證：非 runtime 後端與 local_ai 151 passed；核心接續／升級／學習回歸 64 passed；其餘後端 111 passed，兩個升級版本清單補上 0010 後另測 2 passed。真 API／DB browser 3 passed；前端 Node tests 5 passed、TypeScript／build 通過。本次沒有再呼叫模型，沿用上述 12 次已記錄的合成教材比較；出題 prompt 與品質排序未再調整。

線上確認前端與 `/v1/openapi.json` 回應 200；原登入狀態的教材庫與既有知識地圖均回應 200、revision 一致，browser console 無錯誤。切換後兩份已發布 checkpoint 已清理；另一份失敗 checkpoint 經核對已有相同輸入／分析設定的成功後續工作，也已清理。教材、地圖、題目與成績資料與備份一致；瀏覽器正常刷新登入只更新 session 閒置期限及活動時間。

私人備份及核對證據：`../.studydy-product/experiments/b05-finish-20260920/`。此次已完成日常服務切換，沒有留下需要另外啟用的 legacy 流程。此節記錄 B5-Q 切換；後續 B5-D 狀態見 [assessment-sets.md](assessment-sets.md)。

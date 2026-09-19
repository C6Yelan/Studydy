# 多來源教材建立與增量更新（B3-A／B3-B）

教材可由一份或多份來源初次建立，也能在建立後追加來源，沿用目前版面與 B02 轉檔。每份原檔、每份轉換後 PDF 各自上限 100 MiB；不新增檔數上限，不保存合併 PDF。

## 初次多檔建立（B3-B）

上傳頁接受多選／拖放與混合已支援格式，逐檔顯示驗證及上傳狀態；全部使用既有 draft→sources→revisions 流程。即使只有 PDF，也先進入來源確認頁。上傳、GET 與 reload 不啟動語意分析。

每份檔案保留獨立 Idempotency-Key；部分上傳失敗只重試尚未收到成功回應的檔案，已確認上傳者不重送。確認頁可逐檔預覽、重試轉換、明確移除未封存來源，並調整這次初始來源的順序。顯示份數、全部 ready 後的總頁數與原檔總容量。有 pending／failed 檔或尚未完成的上傳時不自動開始，也不默默略過來源。

使用者確認後提交 `base_revision=null` 與有序 ready normalization 清單，沿用同一 SourceSet、worker、KS v4、發布及來源回查流程。開始後輸入集合固定，後來上傳者不混入。初次多檔建立後可直接使用 B3-A 追加；既有來源不開放重排。B3-B 沒有新增資料庫 migration 或更動模型 prompt／runtime lock。

## API 與輸入

- `POST /v2/materials/{id}/sources` 保存原檔與轉換工作，不自動開始分析。同一 Material 的相同原檔 SHA 明確拒絕；同名不同 bytes 可分別保存。
- 使用者預覽、勾選後，以 `POST /v2/materials/{id}/revisions` 提交 `material-revision-create/v1`、目前 `base_revision`、有序 `normalization_ids` 和 Idempotency-Key。後端從 base 取得原有成員，再附加選定且 ready 的來源。
- SourceSet、run 與 fingerprint 同交易封存；後來上傳的檔案不混入。每 Material 最多一個 active operation。同意圖重播回同 run，包括 head 已切換後；改順序、錯 base、競爭更新回 409。`base_revision=null` 接受一份或多份 ready normalization。
- `POST /v2/material-processing-runs/{run_id}/cancel` 的 closed body 為 `{schema:"material-revision-cancel/v1",base_revision:...}`，僅取消該次追加。重播回已保存狀態；已完成的 run 保留結果。
- `DELETE /v2/materials/{id}/sources/{source_id}` 只清理未被 SourceSet／run 引用且沒有運行中轉檔工作的 staged source。已分析來源可取消勾選，不可刪改已發布集合。

沿用 owner、Origin、artifact 權限邊界。GET、預覽與登入不啟動模型或建立學習紀錄。

## 來源與增量語意

`bundle-manifest/v2` 將集合內的閱讀序號映射到來源與 normalized page；該序號不代表合併 PDF 頁碼。`knowledge-structure/v4` 使用 `source_set_sha256`，不把集合 digest 偽稱為某份 PDF 的 SHA。原檔／normalized／mapping hash、policy、順序與 bundle hash 皆綁定 run／KS，處理與讀取時驗證。

既有單 PDF 第一次追加時建立缺少的 identity mapping，不改寫舊 KS、題目或答案。已保存 v2／v3 依原契約嚴格驗證，沒有驗證失敗後切換 reader 的 fallback。

舊 Evidence／Claims 由已驗證基準重用，以原始與 normalized hash、頁碼、原文、區塊順序及 region 對應到新集合。只對新增來源執行 extraction／必要 OCR／semantic calls。請求包含新 Evidence、既有概念 catalog 及各 Claim 的來源 scope，不反覆送入整份舊頁面 metadata。

沿用 Claim grounding、字面值保護及 Relation validators。模型的 `review_required=true` 只是來源 scope／概念分組的複核提示，不能據此認定教材互相矛盾，也不停止整次更新。可回查的新增 Claims 照常納入，既有 Claims 不改寫；提示以 `source_review_required=true` 與 `SOURCE_REVIEW_SUGGESTED` 保存，品質為 `needs_review`。此版本沒有任意語意修訂或自動衝突裁決引擎。

runtime lock v18 的 material request 為 v3、response 為 v5，記錄來源 scope 與 review flag。bundle policy 為 `contiguous-evidence-new-input/v4`：Gemma 與 command 都使用既有連續 Evidence 分批器、章節邊界及累積概念；每批新增內容目標沿用 1536。Gemma 使用服務端 tokenizer，command 在本機以 ASCII 四字元、其餘每字元估一單位；這不是 Luna 的實際 token 計數，不能宣稱批次邊界完全相同。command 的累積概念不計入新增內容目標，也不以它拒收整份教材；單一區塊不截斷。Gemma model／revision、generation、OCR routing 及既有 context 檢查不變；開發執行器與替代模型仍由私人設定注入。

## 學習進度

新版 session 仍由明確操作 ensure。新舊 Claim 只有文字、來源及區塊定位完全相同且一對一時才承接；有歧義、變更、拆分或合併者不自動承接。

舊 AnswerEvents 僅在記憶體中投影為新版 Claim 的學習證據，交給同一 learning-state reducer。原事件、題目、答案、評分、revision 和時間不改寫，不複製假事件。新增 Claim 沒有作答證據；Concept 仍須全部 Claims 符合既有規則才可 mastered。

舊分頁後續提交仍屬原題，新版重新讀取時可反映到未變 Claim。作答、引導與 head 切換採 Material 範圍鎖；resume 的一致性檢查包含承接後的 progress revision。學習位置只在基準 current Concept 可唯一對應時承接。

## 發布、取消及清理

KS insert、run terminal binding 與 head 更新在同一交易，以 Material→run 鎖序重查 base/head、取消／刪除意圖及 worker token／lease。通過結構與來源驗證、且至少有一筆 Claim 引用新增教材 Evidence 的結果會切換 head，包括 `partial / needs_review`。關係被拒絕、字面值修復及其他品質提示如實保留，作為複核提醒，不要求完美關係，也不阻擋後續追加。

若新增教材完全沒有產生可用 Claim，回報 `NO_USABLE_ADDED_CONTENT` 並保留原圖，不把重用舊內容當成更新完成。來源／結構錯誤、處理或儲存失敗、取消及 base 衝突同樣不發布。

Worker lease 為 10 分鐘，由 checkpoints 及處理期間每 30 秒的存活檢查延展；等待模型期間也會續租，lease 不作為模型執行時間上限。既有 worker 週期檢查過期工作。已保存取消可收斂為 cancelled，晚到結果不得發布。取消意圖不保證立即終止在途模型計算。

## 分批保存與失敗重試

分析完成每一批後，先將 Evidence context、累積語意狀態、精確 Evidence 游標與工作量原子保存，才更新頁數進度。保存位於 private artifact root 的 `analysis/{learner}/{material}/{run}/`；目錄 0700、checkpoint 0600。模型呼叫的輸入、schema、原始回應及 stdout／stderr 也保留在該次 run 的私人目錄，不寫一般 log／Git，也不隨成功或失敗自動清除。

明確重試會建立新 run，僅從同 owner／Material、相同來源 artifact／SourceSet／base revision 與 runtime binding 的失敗作業接續。來源 bytes 仍先經原有 hash 驗證，checkpoint 檢查其 digest 與輸入身分；有損毀時回報 `ANALYSIS_CHECKPOINT_INVALID`，不暗中改成全量重跑。游標是區塊位置，所以最後一頁有多批時，也不會把 `90 / 90` 誤當全部分析完成。

中途失敗重用已完成批次與 Evidence，僅對未完成批次呼叫模型；若只剩 deterministic construction／publication，就重做該步，不需要 OCR、模型連線或新推論。保存的 metrics 包含沿用的分析工作量；新呼叫應以該 run 的呼叫產物核對，不能將沿用結果冒稱新的模型執行。保存紀錄標明 `reused_from_run`，原失敗 run 不改標成功。

只有使用者明確刪除整份教材，才連同該教材保存資料清除；刪除已提交但檔案清理中斷時，啟動會核對 Material 已不存在後補完清理。仍存在的教材，包括失敗作業，一律保留。取消、worker token 與整份刪除意圖仍阻止晚到 worker 發布或重建已刪除資料。

`material-processing-run` 回應帶 `analysis_saved`，失敗頁據此顯示「接續已保存的分析」。沒有保存資料的舊失敗作業不宣稱可恢復。

失敗頁直接呼叫 `POST /v2/material-processing-runs/{run_id}/retry`（空 body、Origin 與 Idempotency-Key）。後端從該次 frozen SourceSet／base run receipts 取回原追加清單與順序，再建立重試 run；不重新讀取 staged 清單來猜使用者意圖，也不納入後來上傳的檔案。回應遺失沿用同 key，即使 head 已切換、舊完整 KS 已清理也可重播。修改來源是另一個明確操作。

若先前失敗原因是完全沒有可用概念／新增知識，重試仍重用 Evidence，但會重試語意步驟，不會卡在反覆組裝同一個空結果。原模型回應仍保留以供查核。

舊流程若僅因 `SOURCE_UPDATE_NEEDS_REVIEW` 停止，重試會比對並沿用該批已保存回應，再繼續尚未完成的批次。比對仍要求同來源與 runtime、完整請求內容相同；catalog 以概念 key 對齊，避免 checkpoint JSON 的字典排序讓等價 catalog 被誤當不同輸入。這是修正後端對既有旗標的解讀，沒有改模型、分批目標或來源綁定，也不回寫舊 KS。

產品提供目前地圖，舊一般 Map 連結在確認有效 run binding 後導向 head。partial 更新顯示「教材更新完成」並附複核提醒；學習紀錄仍以 exact revision 讀回原題。

發布後清理無 StudySession／active run 引用的舊完整 KS，保留 head。被學習紀錄引用的舊 KS 仍是必要資料。小型 run receipts／SourceSet metadata 留作重播和稽核；原檔及轉換 PDF 跨更新重用，不累積每次合併檔。取消勾選本身不會刪除來源。

## 升級與驗證

新增 `0009_material_source_revisions.sql`，0001–0008 不改寫。測試涵蓋 fresh、B02 帶原題／答案／no-safe／completed 的升級，以及 repeat no-op。產品 DB 升級、服務切換仍需獨立授權；寫入 v4 後採 forward repair 或經授權備份恢復，不直接切回不支援 v4 的 binary。

`test_source_revisions.py` 覆蓋增量輸入、重播／競爭、late upload、取消發布、fencing、進度承接與 staged cleanup。`test_source_identity.py` 驗證來源穩定與歧義拒絕；`test_source_revision_migration.py` 比對升級前後舊資料。`test_source_revisions_browser.py` 使用真 API／DB／轉檔／worker 與受控語意 fixture，驗證 desktop／390px、reload、來源與已保存作答；`source-revisions.spec.ts` 驗證佇列和取消不發整份教材 DELETE。

合成測試只證明功能契約。真實替代模型須另外記錄狀態、來源、coverage、呼叫量與限制；`needs_review` 不算 accepted。尚未宣告大型教材容量、任意來源衝突或正式模型品質通過。

### 本次開發驗證（2026-09-19）

工作分支 `be/feature-material-source-revisions`，基線 `5b2bb4a4`，結果對應尚未提交的工作樹。

| 驗證 | 結果 |
|---|---|
| 後端完整測試（不含 browser） | 272 passed；最後邊界修正另以受影響的學習／追加 41 項及結構驗證 40 項通過 |
| local_ai 單元測試 | 13 passed，沒有模型呼叫 |
| 前端 Node tests／typecheck／build | 通過；build 保留既有單一 chunk 超過 500 kB 的提示 |
| 正式建置 browser fixtures | 337 passed；最後取消文案修正再驗 B3-A desktop／390px，2 passed |
| 真 API／DB browser fixtures | 4 passed，含追加、學習恢復、教材庫與轉檔；語意為受控 fixture |
| fresh／B02 upgrade／repeat migration | 通過，舊題／答案與既有欄位不變 |
| 真實開發模型 smoke | 2 次 command transport 呼叫，5 頁初始 → 10 頁追加；來源回查、R1 與 session 保留成立 |
| 品質提示不阻擋更新的修正 | 後端追加測試 12 passed（含連續兩次 partial 仍切換 head、無新增內容失敗）；desktop／390px browser 4 passed；前端 Node tests 5 passed、typecheck／build 通過；沒有新增模型呼叫 |

真實 smoke 使用私人設定的 Luna，初始與追加產物都為 `partial / needs_review`。追加產生 5 個新的走訪概念及跨教材應用關係，但仍有字面值修復和被拒絕的 Relation；當時尚採用舊發布條件，因此未自動替換原 head。使用者於 2026-09-19 明確修正此條件：品質提示不應阻擋有可用新增內容的地圖更新。上面的發布契約已依此修改，歷史 smoke 結果保留原貌；不以它宣稱新條件已做真實模型重跑、accepted 品質或正式 Gemma 驗證。私人請求／回應與結果只保存在工作區 private experiment 目錄。

2026-09-19 經使用者明確授權後，已備份產品 DB 並套用 0009；13 張既有資料表的原有欄位逐列雜湊前後一致。本機前後端已啟動，教材庫、前端資產與 OpenAPI 回應 200，確認載入 B3-A UI／revision API。啟動未呼叫模型或開 Pod；未 commit／push／merge。私人備份及升級核對結果位於 `../.studydy-product/backups/b03-start-20260919T090909Z/`。

### B3-B 完整流程驗證（2026-09-19）

依使用者希望 B3-A＋B3-B 一起完成後再驗收的要求，補齊初次多檔入口；未跨至其他 batch。

- 後端追加／轉檔回歸：32 passed，包含初次多來源排序封存、錯誤成員不產生半套 run、後續增量追加及來源回查。
- 桌面／手機瀏覽器：21 passed，涵蓋初次多檔、部分上傳失敗、相同意圖重試、來源排序、失敗檔案明確移除、B3-A 更新及既有轉檔操作。
- 真 API／隔離 DB／轉檔／worker browser：3 個 Python fixtures passed，內含 PDF＋TXT＋Markdown 初次建立、後續追加與原有作答、單檔轉換；語意為受控 fixture。
- 前端 Node tests 5 passed、typecheck／build 通過。
- 已授權的真實開發模型：重用同兩份 PDF 的十頁節錄，1 次 Luna command 呼叫、46.80 秒，產生 14 Concepts／20 Claims／8 Relations。結果 `partial / needs_review`，有字面值修復與被拒絕的關係，head 正常發布，兩來源均有 Claim 並可回查。沒有宣稱正式模型品質通過。

本對話已知模型實跑累計 9／12 次、468.75／1800 秒。B3-B 私人測試紀錄位於 `../.studydy-product/experiments/b03-initial-20260919/`，不進 Git。產品沿用 schema 0009，無新增 migration；確認無進行中工作後，已以既有 manage.py 重新載入服務供驗收。

### 私人驗收限制修正（2026-09-19）

使用者在 90 頁教材分析至第 83 頁時收到 `SEMANTIC_INPUT_TOO_LARGE`；私人 command 設定仍有 60,000 bytes／12 calls／180 秒的實驗限制。依使用者要求直接刪除這三個欄位與執行器檢查，也移除 command 輸出的 1 MiB cap；未改為 `null` 或另一組上限。等待模型期間增加 worker 續租，避免把 lease 到期當成分析逾時。取消與 worker token／publication 防護保留。

25 項 command／semantic 測試、33 項來源／worker／轉檔回歸通過；僅使用本機合成工具與隔離 DB。本次無真實模型呼叫，未重跑使用者教材。私人設定已備份及更新，服務重新載入，既有失敗紀錄與原檔保留。

### 分批流程修正（2026-09-19）

移除私人輸入上限時曾錯將 command 的批量判斷改為永遠成立，導致使用者的 90 頁全部在一次 Luna 呼叫送出。這筆結果確實完成儲存及來源驗證，但不能拿來與原 Gemma 分批流程作直接品質比較，也不能據此斷言 Luna 品質退步。

現已恢復共用分批器；只對「新增內容」估算批量，保留累積 catalog 和完整來源區塊。此修正不恢復已刪除的私人輸入大小、呼叫次數、執行時間限制。runtime lock 由 v17 升為 v18，明確記錄批量政策變更；已有產物與舊 run 身分不改寫。

84 項語意／分批／結構／local_ai 測試、37 項來源／追加／轉檔回歸通過，包含合成 90 頁多批處理、catalog 延續、Evidence 不重複不遺漏，以及大型 catalog 不觸發私人容量拒絕。本機服務已載入修正，原有地圖仍可讀取；未呼叫真實模型，尚未重新評估模型內容品質。

### 合併錯誤與失敗資料保存修正（2026-09-19）

使用者的 90 頁作業在最後記錄進度 90／90 時失敗；舊程式只保留固定錯誤碼，模型回應已由 `TemporaryDirectory` 清除，沒有 checkpoint 或正式 KS 可救回。這筆歷史失敗未改標成功，也沒有重跑模型；因原回應遺失，不能斷言已逐筆回放確認其實際觸發原因。

本機另已重現並修正兩個 deterministic construction 錯誤：跨批沿用 key 時，交換主名稱／別名會把主名稱放回 aliases，最後遭 validator 拒絕；不同模型 key 產生完全相同 canonical Concept 時會發布重複 ID。現在合併時排除主名稱、建構時去除完全相同的 canonical 節點，保留所有 key 到 canonical ID 的關係，不放寬來源或地圖完整性驗證。

保存／接續機制與上述修正一併實作。小型 fault injection 已確認：同頁第二批失敗只重試該批；組裝／發布失敗可在不建立模型 client、不執行 preflight／OCR／語意呼叫下完成；保存失敗在模型呼叫前停止；checkpoint 損毀不靜默重跑。也驗證成功與失敗保存資料都留存，只有明確刪除教材才清除。

驗證：53 項分批／結構／command 測試、78 項來源／取消／刪除回歸通過。完整後端回歸先為 288 passed／1 failed（preflight 後取消時機）；修正後受影響 37 項通過，最後離線恢復／損毀檢查 3 項通過。桌面／手機 browser 21 passed，真 API／DB browser 3 個 fixtures passed；前端 Node tests、typecheck／build 通過。最後的 command 8 項測試另確認隔離 cwd 保持不變、成功／無效 JSON 原始產物保留，以及明確刪除教材才清理其 cwd。全部使用本機合成工具／隔離 DB，沒有真實模型呼叫。無新增 DB migration，未 commit／push。

重試入口的後續驗證：6 項 retry 回歸通過，另以兩頁小檔確認無可用新增語意時重試不會倒退頁數而失敗。初次／追加的真 API 測試均確認保留封存順序、忽略晚到檔案、舊 KS 清理後同 key 仍可重播；桌面／手機 21 項 browser 再通過，含直接重試與回應遺失使用同 key。修正已載入本機服務，沒有重跑真實模型。

### 複核旗標不再阻擋更新

使用者後續追加同系列教材時，第 28 批回應只有 `review_required=true`，未提供互相矛盾的具體對照；舊程式卻以該布林值直接停止整次更新。現改為保存複核提示、保留原 Claims 並納入可回查的新增內容，結果仍標 `needs_review`，不冒稱內容已驗收。

28 項來源／追加／身分測試通過；調整回應重用時的 catalog 排序比對後，4 項相關回歸通過；52 項結構／pipeline／身分測試通過。對實際保存的第 28 批只做本機記憶體重播，確認 3 條新增 Claims 可納入、原有 Claims 不變，尚餘第 126–128 閱讀頁的 13 個區塊。這次沒有模型呼叫、沒有改寫失敗紀錄或先行發布未完成的地圖。

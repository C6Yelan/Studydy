# 系統架構

[文件入口](../README.md) · [教材處理](materials.md) · [學習與評量](learning.md)

## 資料流

~~~mermaid
flowchart LR
    UI[React] --> API[FastAPI]
    API --> DB[(PostgreSQL)]
    API --> Store[(Artifact Store)]
    DB --> Worker[教材 Worker]
    Worker --> Convert[隔離轉檔]
    Worker --> STT[本機語音辨識 provider]
    STT --> Convert
    Worker --> Evidence[原生文字／Unlimited-OCR]
    Evidence --> Model[語意模型服務]
    Model --> Validate[來源與結構驗證]
    Validate --> Index[建立教材檢索索引]
    Index --> DB
    UI --> Retrieve[依問題檢索原文]
    DB --> Retrieve
    Retrieve --> Answer[有界回答與引用核對]
    Validate --> Store
~~~

Nginx 代理同源/v1 API。API 處理身分、讀寫邊界及建立工作；worker 執行轉檔、分析／檢核與題組準備。PostgreSQL 保存狀態與 metadata，檔案位於 STUDYDY_DATA_DIR/artifacts。

教材處理由 STUDYDY_MATERIAL_WORKERS 設定執行緒數（預設 1），題組準備與定期資料復原各維持一條執行緒，不依賴模型種類或 Luna 測試旗標。啟動復原只執行一次，完成後額外教材執行緒才開始領取；來源正規化與筆記沿用資料庫互斥領取，章節 request_gate 互斥保留；每個領取與發布仍須通過原有 PG lease／token 與來源版本檢查。這項排程隔離不代表模型能同時推論，實際容量仍由既有 provider 控制。

概念難度與學習診斷在讀取時由既有教材、學習計畫及作答事件計算。難度只使用先備關係與重點數；診斷使用確定性模板。診斷只保留教材範圍，提供完整重點與真實累計／補強連對進度；公開查詢核對擁有者及固定教材版本，不寫入額外快取或事件。

起始程度保存在教材，開始／接續入口在同一個 Material → Study 鎖序下選擇既有題組、跨概念診斷或後續概念；開始意圖的回執保存在 StudySession，重送不重建題組。跨概念 `material_diagnostic` 的整組目標為空，每個成員仍保留自己的概念、重點及來源範圍，由既有題組 worker 處理，不套用單概念補強 cycle。

`learning_goals`（0015）只保存目標設定、重排基準、更新提示已讀位置與當日計量基準，不保存未來日程或概念清單。會影響可測範圍／作答的寫入先凍結尚未保存的當日基準；沒有目標時直接退出，當日已凍結也不重播整份進度。日初範圍固定配額，今日成果則計入所有真實新完成重點。目標及分天預覽的 GET 保持唯讀，首頁／島嶼共用獨立的目標摘要入口，不加入全站經驗值彙總。

`material_notes`（0017）按擁有者、教材版本與種類保存發布內容、模型契約快照及生成租約。教材執行緒領取工作，沿既有 provider 的文字通道請求；只有引用、逐段內容核對與涵蓋檢查（完整筆記為全部重點，重點整理為每個概念至少一個核心重點）通過才原子替換發布內容。寫作與核對都保留引用的原檔／頁碼、區塊順序及位置，不把表格拆成無位置的文字片段。內容核對逐一要求每個條列項、文字、公式、表格標題／表頭／各列與醒目區塊標籤的結果；不符則只重生該段並再核對；完整筆記仍不符或核對失敗不發布，重點整理則由程式刪去未解說重點與仍不符段落，沒被解說的概念局部補寫一次；被核對擋下的批次在同一工作內最多共三次嘗試，仍不過才失敗。兩種筆記都保存固定計畫（重點整理含已挑選的重點）與已驗證批次，相同契約恢復時從未完成部分接續；契約變更後的重試清除未發布計畫／批次並使用目前 lock，既有筆記與註記保留。全部完成後才發布並移除暫存批次內容；分區進度隨工作保存。過期 lease 不得發布。`generation_diagnostics`（0018）保存最近一次發布或失敗的刪減／失敗摘要碼，與日誌同一份，不含教材或核對理由全文，不經 API 回傳。`note_annotations` 隨教材保存，保留原段落快照與來源／重點對應，不受筆記重生取代；有註記引用的舊教材版本不得清理。讀取使用一致唯讀 snapshot，不建立工作。

後端管理本機 OCR 子程序。外部語意模型獨立運行；模型離線時仍可登入及讀取已保存內容。

## 模組

| 位置 | 責任 |
| --- | --- |
| [runtime/api](../backend/src/runtime/api/) | HTTP 契約、認證、Origin、錯誤與公開投影 |
| [document_normalization](../backend/src/document_normalization/) | 沙箱轉檔、格式檢查與來源 mapping；`media.py` 處理圖片組、音軌與逐字稿頁面 |
| [media_sources.py](../backend/src/runtime/media_sources.py) | 分段上傳、影片抽音軌、YouTube 讀取與限制、送本機辨識及媒體來源轉換 |
| [pdf_evidence](../backend/src/pdf_evidence/) | 擷取、Evidence 與分析流程 |
| [knowledge_map](../backend/src/knowledge_map/) | 概念、Claim、Relation、檢核與確定性建構 |
| [learning_adaptation](../backend/src/learning_adaptation/) | 題組、私密答案、評分、進度與導覽 |
| [knowledge_publication.py](../backend/src/runtime/knowledge_publication.py) | 協調知識結構發布、版本與工作驗證、索引建立／補建及章節排程 |
| [learning_retention.py](../backend/src/runtime/learning_retention.py) | 刪除前每日計量、永久徽章與跨裝置原子慶祝領取 |
| [learning_activity.py](../backend/src/runtime/learning_activity.py) | 學習活動彙總、XP、等級、連續天數、徽章與今日任務 |
| [local_ai_config.py](../backend/src/runtime/local_ai_config.py) | 讀取環境設定與 runtime lock，提供共用 AI 設定入口 |
| [runtime/storage](../backend/src/runtime/storage/) | 資料存取、migration、完整性檢查與檔案恢復 |
| [storage/card_moves.py](../backend/src/runtime/storage/card_moves.py) | 查詢卡組承接目標及原始卡片身分 |
| [workers.py](../backend/src/runtime/workers.py) | 工作領取、lease、執行與恢復 |
| [local_ai](../local_ai/) | OCR adapter、protocol 與模型執行設定 |

HTTP 層處理請求邊界，runtime 用例協調流程，learning_adaptation／knowledge_map 處理學習與內容規則，storage 負責資料存取；現有查詢與索引建構仍保留跨層依賴。

目前保留三組功能內部循環：

- `learning_adaptation` 的 `answer_events`、`assessment_sets`、`assessment_state`、`inherited_progress`、`study_sessions` 共五個模組包含實際互遞迴的歷次作答與題組投影，拆分須先確認責任合併及交易／回寫順序，暫時保留。
- `podcast_cues`、`podcast_video_motion`、`podcast_video_plan` 保留字幕、時間窗與分鏡驗證的功能內部共用關係，拆分時須一併維持這些契約。
- `research_reading`、`research_sources`、`research_web` 共用 HTTP 節流、快取與正文取得狀態，拆分會改變測試替身的攔截位置，須連同狀態所有權與呼叫路徑一併處理，暫時保留。

功能路由直接共用 [request_context.py](../backend/src/runtime/api/request_context.py) 的 session、query 與冪等鍵檢查；[study_workspace.py](../backend/src/runtime/study_workspace.py) 組合學習查詢，HTTP 層只投影公開資料。題組的查詢與完成判定位於 [assessment_state.py](../backend/src/learning_adaptation/assessment_state.py)，建立、交卷及生成工作由 `assessment_sets.py` 負責。

[database.py](../backend/src/runtime/storage/database.py) 管理連線池與每次交易，`tables.py` 只做 ORM 映射。[artifacts.py](../backend/src/runtime/storage/artifacts.py) 統一原檔、轉檔及媒體的保存、完整性檢查與清理；PDF 預覽仍保留自己的型別限制。一般讀取每次整檔核對 DB 雜湊；媒體 Range 串流（Podcast 音訊／影片、來源原音、MP4 匯出）在同一程序內，對裝置／inode／大小／mtime／ctime 與 DB 雜湊皆未變、且已整檔核對過的檔案只核對身分與大小，不每次重讀；物件寫入後為 0400 且只整檔替換，任何改寫都會使快取失效。交給 provider 的音訊（影片、對齊）與下載仍每次整檔核對。帳號空間由 [storage_usage.py](../backend/src/runtime/storage_usage.py) 計算：該帳號全部 artifacts 的 `size_bytes`、`analysis/<learner>/` 目錄與尚未收齊的分段上傳；會新增檔案的入口在接受請求時檢查一次。發布流程由 `knowledge_publication.py` 協調，`knowledge_structures.py` 保留讀取與清理。讀取索引的建立與補建由 `knowledge_publication.py` 負責；`knowledge_reads.py` 保留概念索引建立與讀取投影，`card_sources.py` 處理卡組導覽與分批顯示。

[provider_http.py](../backend/src/runtime/provider_http.py) 管理既有 provider 傳輸，工作續租與取消仍屬各執行流程。內容序列化使用 `content_identity.py`；講稿沿用 `podcast_script.py` 的獨立雜湊規則，避免改變已保存的身分。

匯出入口位於 `runtime/api/export_routes.py`，文件格式由 `export_documents`、`export_cards`、`export_map`、`export_podcast` 處理。各入口沿用既有授權讀取；卡片與地圖圖片由前端現行視覺元件產生，後端核對完整範圍並產生 PDF／Anki／ZIP。所有 PDF 在後端生成，臨時匯出不建立新的持久 artifact 或模型工作。卡組用私有磁碟暫存承接分批卡面，每份卡面容量、每概念頁數與每人進行中工作數均有上限；檔案鎖維持上傳／下載／清理互斥；生成完成後直接串流檔案，避免把整組 PNG 與輸出副本同時放在記憶體。

## 模型與驗證

模型提出觀念、重點、關係及題目候選；程式驗證來源、頁碼／區塊、字面值、schema、關係循環、授權與答案保密。Structured Output 只保證可解析，結果須完成來源綁定與驗證，再計算 revision 並發布。

只有 prerequisite 關係影響建議學習順序；其他型別保留語意與方向。定義見 [structure_rules.py](../backend/src/knowledge_map/structure_rules.py)。

模型、revision、套件、prompt 與 token 預算以 [runtime-lock.json](../local_ai/runtime-lock.json) 為準。部署位址與 token 由環境提供，工作與題組保存建立時的快照；讀取時核對該快照，不以現行設定替換生成身分。

一般讀取核對 metadata；發布及實際使用檔案時核對 bytes。來源檔損毀會阻止該檔案使用或新結果發布，不會自行刪除已保存地圖。

## 授權與一致性

- Email 正規化後唯一，密碼以隨機 salt 的 scrypt 保存；參數見 [learner_session.py](../backend/src/runtime/learner_session.py)。
- API 以 HttpOnly session cookie 與 owner scope 授權；寫入檢查 Origin，私人回應使用 private/no-store。前端身分提示不授予資料權限。
- 登出使 client 與私人畫面失效，延遲回應不能恢復另一個帳號資料。
- 修改密碼（`POST /v1/account/password`）須核對目前密碼，成功後撤銷本人其他所有 session，目前 session 沿用原 token（重新簽發會讓同一瀏覽器其他分頁進行中的請求收到 401 而被登出）；目前密碼錯誤整筆不寫入。同帳號 15 分鐘內錯誤 5 次即回 429 `TOO_MANY_ATTEMPTS`，紀錄只在 API 程序記憶體，重啟歸零。`GET /v1/account/sessions`（`account-sessions/v2`）列出本人未撤銷且未逾期的登入：只公開 `session_id`、裝置標籤、登入時間與最後使用時間，目前這個排第一。`DELETE /v1/account/sessions/others` 撤銷其他 session；`DELETE /v1/account/sessions/{session_id}` 撤銷本人另一個登入，目前這個回 400（改用登出），不屬於本人或不存在一律 404。三種寫入先鎖帳號再鎖目前 session，避免兩個裝置同時操作互鎖。
- 裝置標籤在註冊／登入時由伺服器從 User-Agent 解析成「瀏覽器 · 系統」（如 `Chrome · Windows`），只存標籤，不存原始 User-Agent 或 IP；認不出為 NULL。最後使用時間由 session 解析記錄，距上次超過 5 分鐘才寫入，不延長 session 期限；0013 前的 session 以 `updated_at` 代替。
- Material／Study 鎖、版本與唯一約束保護並行操作；lease／worker token 阻擋過期結果發布。
- Progress／resume 在同一唯讀 snapshot 投影，整組交卷在同一交易保存。
- DB 與檔案透過 staging、quarantine 及 reconciliation 保持一致。

Migration runner 核對 [SQL序列](../backend/migrations/) 與 checksum，每版 schema 及帳本同交易提交。API 完整定義以 [OpenAPI](http://127.0.0.1:4173/v1/openapi.json) 及 [models.py](../backend/src/runtime/api/models.py) 為準。

新資料庫初始化時寫入 `schema_baseline` 標記；每次啟動核對 baseline 身分與前七份初始化 SQL 的 digest，再驗證 migration 帳本並套用後續版本。已發布的初始化 SQL 不得改寫。

Gemma 路徑以服務端 tokenizer 核算輸入，依 runtime lock 的上下文容量分配剩餘輸出；Luna 暫用路徑以 o200k 估算，並受 64K 上限限制。分析／教材檢核至少預留 8K 輸出，出題／題目檢核至少預留 16K（若明確設定較小上限則依該值）。只變更額度時可接續已驗證的分析 checkpoint，原工作快照維持不變。截斷失敗僅保存階段、結束原因與 token 數供排查。

## 教材檢索與選讀

從主題建立教材與補充學習共用 `topic_scopes`：先以 Luna 整理可編輯的觀念與具體重點／細節，確認前不建立研究工作、不查外部來源。補充規劃綁定原教材與 revision；確認時再次核對，教材已更新便要求重新規劃。`POST /v1/materials/{id}/research` 已移除，兩種入口都由 `POST /v1/topics` 建立規劃，再由 approve 建立研究。

新研究使用 `material-research/v3`；舊查詢與已核對來源保留供查看及選用，但舊搜尋狀態不能接回新 worker 執行，須重新確認需求後建立新研究。沒有自動轉接、舊搜尋開關或另一套建立入口。

研究逐項沿用確認過的 topics，不再由搜尋模型另拆一份範圍。初始查詢一律優先論文，以自然語言問題呼叫 OpenAlex `search.semantic`，先篩開放取得且有摘要的作品；實際全文授權與版本仍依 locations 核對。優先初篩已知有授權取得位置的候選，同組保留索引的語意排序；Luna 再依摘要評估用途，取得正文後才判斷支持程度。正文區段由模型逐批定位，不以標題或字面排名排除同義內容。搜尋範圍限定論文與英文 Wikipedia，不納入一般網站、部落格或官方技術文件。涵蓋判定不能跳過已排定搜尋、初篩、應讀正文或限流重試；背景與排除來源不必全部深讀。缺口檢查選定的覆核優先執行；沒有可行新方向或持續無新增證據時，整理部分成果並如實保留缺口。

已移除 Crossref 的泛關鍵字搜尋、標題詞數加權與混合管道；Crossref DOI 摘要查詢仍供缺摘要的論文使用。論文先試正式 metadata／原頁公布的同版本全文入口；第一次閱讀前若仍取不到，可採索引已列明且有適當授權的其他版本，記錄實際版本與原先版本，不把作者稿冒充出版版。已讀原檔及明確綁定的 arXiv 版本不能因此換檔，也不請模型猜下載網址。OpenAlex 語意搜尋最多 50 筆，`locations.license` 不被該端點接受；目前使用 `is_oa:true,has_abstract:true`，不能將 HTTP 200 或開放取得標記當作已核對全文授權。API key 可提高免費額度，設定仍透過既有環境變數，不進入來源紀錄。

索引只給已授權的論文頁而沒有直接 PDF URL 時，保留該位置與授權，沿原頁公布的 PDF 取得全文；下載後仍核對論文身分、版本與 bytes。優先保留 PDF 的原圖表與排版；PDF 不可取得時，若原頁自身有可擷取的論文章節正文，可改用 HTML，不將它當成只有摘要的介紹頁。同次閱讀不重試已失敗的相同入口，結構化摘要與導覽標題不算正文。索引授權與頁面明示授權分別記錄，不把前者偽稱為後者。原頁公布的 HTTP 連結與搜尋連結一樣先正規化成 HTTPS，沒有放寬 DNS、TLS 或私人位址限制。只有介紹或摘要而未取得正文時，不進入正文驗收。

研究來源先依學習需求選讀，核對後的原文範圍傳到來源正規化。第一次成功取得並核對的 PDF 或 HTML 擷取正文與研究進度同交易存入本機 `material_research_documents`；後續選讀、重啟與加入教材沿用相同 bytes，仍核對研究歸屬、大小及 SHA-256。HTML 保留原頁 hash、來源／授權與文字快照，不以網站重下載取代已讀內容。候選 JSON 不帶原檔，API 不公開快照收據。成功建立教材版本、取消未建立教材的主題規劃，或刪除研究後清除準備快照，已匯入的 Artifact 保留。完整原檔與正規化 PDF 保留；分析只擷取選定頁面的必要區塊，來源集合中的連續頁碼仍對應原文件頁碼。找不到引用原文時回報範圍定位失敗，不改成全文分析。

研究品質以能支持的教學內容與限制判斷，不以耗時、篇數或來源類型設通用淘汰門檻。定位／閱讀會一起選取重要結論所需的方法、研究對象、結果與限制；反例、相反結果、單篇研究、預印本、小樣本、理論與個案都可採用。涵蓋核對區分模型預測、觀察關聯、因果證據及引用他人的結果，不把重複引用算成獨立佐證。已有答案的子題若仍有會改變解釋的具體疑點，可以繼續讀方法或補查相反結果；保留已支持內容，不為追加查證而先撤掉來源。研究限制不自動升級成需求缺口；使用者未要求精確數值、普遍外推或完整實作時，不額外要求。原文明確呈現的分歧與尚無定論也可成為已涵蓋內容，不能為得到一致結論而排除來源或無限搜尋。

閱讀沿用已核對的來源類型，再依正文判斷證據強度，不因單一片段重新否定文件類型。子題原文可完整放入上下文時一起核對，避免跨批缺少上下文被誤判為沒有證據。

涵蓋快取綁定判讀政策；續接舊研究時若政策不同，只清除涵蓋與停滯判斷的快取，保留原檔、已讀引用與來源，再按目前規則重核。既有完成紀錄不自動背景重寫。

知識結構發布前建立 `material_retrieval_indexes` 與 `material_passages`。PostgreSQL GIN 索引使用中文雙字、技術術語、概念名稱與別名；它是可重建的取材投影，不是第二份教材事實來源，也沒有另啟 embedding 模型。問答、研究規劃與 Podcast 共用此索引。問答用 Luna 補查術語以處理不同用詞，最多再取一次來源；每個查詢都固定 owner、教材與 revision。

索引與知識結構同一交易發布，索引失敗不更新教材 head。索引階段可沿已保存的分析與檢核結果重試，不必重新呼叫模型。新來源綁定為 `structure-input-binding/v2`／`source-set/v2`／`bundle-manifest/v2`；舊格式不轉接、不自動遷移，原始檔案不刪除。

來源顯示、run完成摘要與概念疑義為獨立公開投影，不改已發布結構hash。草稿目標在教材發布時建立排程；跨版筆記以content_revision回查真正顯示稿件的來源。

帳號空間細項與總量使用同一次SQL及同一次分析目錄掃描，回應內加總一致；DB／磁碟不是跨系統原子快照。

作答 presence 存於 PostgreSQL `assessment_presence`，鍵為 learner/material/browser。問答 API 以可信登入帳號及對話教材檢查，並連接題組即時狀態；交卷與回答在同交易完成，completed 題組不再阻擋提問。題組刪除以外鍵 cascade 清理 presence。

Podcast 步驟時間：講稿／音訊放在分集 step_times，對齊／影片使用工作表 started_at／finished_at。只讀 provider /jobs/progress 用 job_id＋job_token 對應活動請求，觀測真正等待鎖／持鎖狀態；回應不含教材、帳號或台詞。段落略過只改發布取捨，不改核對提示或 runtime lock。

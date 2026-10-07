# 教材處理

[文件入口](../README.md) · [使用指南](usage.md) · [系統架構](architecture.md)

## 來源與轉檔

教材是具名的來源集合。單檔與轉換後 PDF 上限皆為 100 MiB，格式能力由/v1/source-capabilities 宣告。

| 格式 | 處理與定位 |
| --- | --- |
| PDF | 驗證原檔、建立預覽與 mapping，使用原頁碼 |
| DOCX | LibreOffice Writer 轉 PDF；使用轉換後頁碼，段落對照可能有歧義 |
| PPTX | LibreOffice Impress 轉 PDF；排除 hidden slides／notes，保留原投影片編號 |
| DOC／PPT | 檢查二進位 Office 格式再轉 PDF；僅提供轉換後頁碼 |
| UTF-8 TXT | 有界換行排版；保存原行號及 PDF 頁碼 |
| Markdown | 限制 HTML 排版；raw HTML 顯示為文字、不載入圖片，保存 block 及 PDF 頁碼 |

原檔、PDF 與 mapping 各自保存 hash。下載使用原 MIME 與 attachment，PDF 預覽使用獨立入口。解析在無網路 Bubblewrap 子程序內執行，唯讀掛載 runtime 及輸入，僅輸出／暫存可寫。資源限制、版本檢查與禁止的主動內容見 [converter.py](../backend/src/document_normalization/converter.py) 及 [renderer.py](../backend/src/document_normalization/renderer.py)。

## 草稿與來源快照

建立草稿後，各檔案以獨立 Idempotency-Key 上傳並排入轉換。使用者確認就緒來源與順序，後端同交易封存 SourceSet、bundle manifest 與 processing run；之後上傳的檔案不混入已建立工作。

相同意圖重播不重複建立；輸入、順序或 base revision 不同則拒絕衝突。未被使用的來源可移除；只被 failed／cancelled 工作引用的來源可退出清單，內部保留原檔與封存關係。正在處理或已發布結果使用的來源受保護。重新上傳使用新的意圖，已移除 normalization 不能用於新分析。

## Evidence 與分析

PDF 原生文字足夠時建立 native Evidence；重要圖片區域使用 Unlimited-OCR。混合頁保留原生文字並補入 OCR 結果，未恢復內容留下複核提示。

Evidence 保留教材、1-based 頁碼、區塊、定位及技術字面值。換行合併依幾何與段落結構，不跨欄猜讀序。

語意請求按章節與 Evidence 分批，以實際 tokenizer 核對 prompt、概念目錄與輸出預算。模型引用完整 Evidence handle；程式還原來源關係並驗證 Claim、Relation 及 Path。設定以 runtime lock 為準。

每批保留所有已知概念的 key、名稱與別名，供模型重用身分；只有同章節，或本批文字／章節標題提到其名稱、別名或 key 的概念，才附上既有 Claims 與來源 handle。省略的是傳輸視圖中的細節，canonical state、checkpoint、原文與引用仍完整保存。這可降低重送歷史全文的成本，但名稱比對不保證辨識所有同義詞或跨章節矛盾；模型品質仍須另行驗收，完整名稱索引也仍會隨概念數成長。

相關 Claims 仍造成超額時，該批退回完整概念身分索引與新來源，不附歷史 Claims 細節，再核對實際容量。省略細節不刪後端已保存的知識；若連索引與單一原文區塊都放不下，仍會失敗，不提高 64K 上限或截斷來源。

分批容量探測從 8 個 Evidence 起逐倍擴大，遇到上限後縮小搜尋範圍，避免每批先把所有剩餘教材送到 tokenizer。最後仍核對實際請求容量、保留輸出空間，並盡量沿章節邊界切分；單一 Evidence 不截斷，放不下時如實失敗。

單一尚未被 Claim／Relation 引用的 OCR 區塊超過輸入額度時，可從原 PDF 的同一區域裁切後重辨識一次。新文字建立新的 Evidence ID，保留原頁、區域與閱讀順序；新工作保存重辨識紀錄及 checkpoint，原失敗工作不改寫。仍放不下、已被引用、原文非 OCR 或儲存失敗時如實停止。OCR 次數包含這次區域重辨識，因此可能大於頁數。現行 OCR 防重複窗口為 256，並拒絕未正常 EOS 結束的生成；這是執行恢復機制，並非內容品質驗收。

## 檢核與發布

初次分析與追加結果都經教材檢核。模型提出整合、案例歸屬、別名、文字及關係修正；程式核對覆蓋、來源、先備關係與字面值。覆蓋／歸屬錯誤可有界補正，無來源支持的修改保留原文或拒絕發布。

檢核請求超過容量時，分開待檢核概念，完整來源脈絡在各組保留；若單一概念連同來源仍放不下，就明確停止。已驗證批次可重用。編輯編號限於本組提供的 handle，同類編輯中同一 handle 最多一筆；越界或重複時回饋具體編號，最多補正一次，再次失敗仍拒絕，不任意挑選一筆套用。

不同概念可能共用同一 canonical Claim。跨段提出相同改寫時合併來源與理由；改寫互相衝突時不套用任一份，保留原文並記錄待審查項目。既有及檢核新提出的先備關係都會保護兩端概念，不允許同時把端點合併成同一個學習點。這些保留規則不放寬單一回應的編號、引用或來源驗證，也不把 `needs_review` 視為品質已驗收。

有可用新增內容的 partial／needs_review 結果可發布；無可用新增內容、取消或失敗時保留目前 head。已發布教材可由/v1/materials/{material_id}/review 建立只檢核的新版本，尚無獨立 UI 入口。

## 追加與接續

追加只分析新增來源，再整合檢核；發布同交易更新知識結構、run 與教材 head。教材庫開目前 head，學習依 exact revision 恢復。被學習或活動工作引用的結構保留，其餘完整結構可清理；來源檔跨版本重用。

每頁成功整理後，先原子寫入頁面 Evidence checkpoint，再更新處理頁數；快照綁定 owner、教材、來源集合、原來源頁碼與 runtime，保留完整原文及 Evidence 身分。失敗或中斷後可重用已保存頁面，不重新渲染或呼叫 OCR；尚未成功擷取的頁面仍需重試。

章節導覽名稱超過 512 字元時使用首行或帶省略符號的顯示標籤，完整標題原文仍保存在 Evidence，不截斷來源或改寫其 ID。章節建構失敗回報 `DOCUMENT_EVIDENCE_INVALID`，已保存頁面仍可接續。

語意階段 checkpoint 保存累積狀態、游標及執行快照。相同 owner、教材、輸入與分析設定的重試可重用已完成批次；只剩確定性建構／發布時不再推論。損毀或設定不符時停止，不靜默重跑。

私人 analysis archive 保存請求與回應；重用和新增呼叫分開計數。Checkpoint 只在 DB 確認發布後清理，失敗由 worker 補做，過期 worker 不能重建已結束工作的狀態。

## 取消與刪除

取消更新保留已發布地圖與學習；刪除整份教材則先保存意圖、阻止新工作及過期發布，再清理來源、分析與學習資料。檔案先進 quarantine，DB 回滾時還原、提交後刪除，清理可重試且不影響其他教材。

取消不保證外部服務立即停止已送出的計算。教材改名只更新顯示名稱，不改 hash 或學習紀錄。

## 讀取與連線

每個程序依 DSN 重用 SQLAlchemy 連線池，基礎 5 條、臨時最多再加 10 條；交易與 Session 仍按操作建立。鎖等待 5 秒、SQL 60 秒的設定使用 SET LOCAL，交易結束後恢復；借出前檢查連線。隔離測試刪除自己的 DB 前先釋放該 DB 的池。

Podcast 與補充搜尋的唯讀操作使用教材共用鎖，追加、取消及其他修改仍使用排他鎖。搜尋紀錄清單只讀摘要，候選、授權與取得資訊由單筆讀取載入。知識結構讀取仍核對內容、run binding 及來源關係，不掃描原始 PDF，也不以跳過核對或公共快取換取速度。

## 公開來源搜尋與取得

主題建立與補充學習共用搜尋：AI 只產生關鍵詞，後端並行查詢 OpenAlex、Crossref、Python 文件與 MDN。Python 搜尋索引在程序內快取 24 小時。官方文件的多詞查詢至少需要兩個標題詞符合，避免只命中 `generation`、`light` 或 `testing` 就將無關技術文件排入教材來源；其餘候選依標題關鍵詞重合數排序。這是相關性篩選，不是內容正確性或教學品質驗收。

同 DOI（不分大小寫、移除 DOI URL 前綴）或同來源 ID 合併成一筆，保留原候選 ID 與已取得來源。搜尋服務出錯時保留其他結果並顯示部分搜尋失敗；全部服務失敗仍回報失敗。分頁 cursor 保存各學術服務的進度，也能接續既有 OpenAlex cursor。多個平台索引同篇文件不代表多份獨立證據。

論文仍只匯入目前允許的 CC BY／CC0 PDF；Crossref 的授權必須已生效且對應全文版本，不把 TDM 條款或相似度檢查專用連結視為一般開放授權。HTTP 全文 metadata 連結只嘗試升級 HTTPS，不允許降級或私人網路位置。取得失敗時最多嘗試三個同版本授權位置，成功後仍核對 PDF、DOI／標題，記錄實際版本與檔案 hash。不自動將預印本替換出版版本；403 與 429 分別顯示網站拒絕下載與限流，429 依 Retry-After 暫停該主機請求。

選填設定（僅放私密 `.env`，不要提交金鑰）：

- `STUDYDY_OPENALEX_API_KEY`：以 Authorization header 傳送，提高免費額度；跨主機重新導向不轉送金鑰。
- `STUDYDY_SEMANTIC_SCHOLAR_API_KEY`：有 key 才加入 Semantic Scholar 搜尋，單程序請求間隔至少 1.1 秒；仍需明確全文授權。
- `STUDYDY_UNPAYWALL_EMAIL`：有聯絡 email 且已選論文下載失敗時，才向 Unpaywall 查詢同 DOI 的其他全文位置；email 會隨請求送出。未設定時不啟用。

SearXNG 目前只完成隔離測試，未納入正式服務；部分上游回傳 CAPTCHA／限流。上述串接沒有啟用全文語意交叉比對，搜尋結果摘要不會當作 Evidence。

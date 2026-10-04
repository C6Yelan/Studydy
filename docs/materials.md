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

## 檢核與發布

初次分析與追加結果都經教材檢核。模型提出整合、案例歸屬、別名、文字及關係修正；程式核對覆蓋、來源、先備關係與字面值。覆蓋／歸屬錯誤可有界補正，無來源支持的修改保留原文或拒絕發布。

有可用新增內容的 partial／needs_review 結果可發布；無可用新增內容、取消或失敗時保留目前 head。已發布教材可由/v1/materials/{material_id}/review 建立只檢核的新版本，尚無獨立 UI 入口。

## 追加與接續

追加只分析新增來源，再整合檢核；發布同交易更新知識結構、run 與教材 head。教材庫開目前 head，學習依 exact revision 恢復。被學習或活動工作引用的結構保留，其餘完整結構可清理；來源檔跨版本重用。

Checkpoint 保存 Evidence、累積狀態、游標及執行快照。相同 owner、教材、輸入與分析設定的重試可重用已完成批次；只剩確定性建構／發布時不再推論。損毀或設定不符時停止，不靜默重跑。

私人 analysis archive 保存請求與回應；重用和新增呼叫分開計數。Checkpoint 只在 DB 確認發布後清理，失敗由 worker 補做，過期 worker 不能重建已結束工作的狀態。

## 取消與刪除

取消更新保留已發布地圖與學習；刪除整份教材則先保存意圖、阻止新工作及過期發布，再清理來源、分析與學習資料。檔案先進 quarantine，DB 回滾時還原、提交後刪除，清理可重試且不影響其他教材。

取消不保證外部服務立即停止已送出的計算。教材改名只更新顯示名稱，不改 hash 或學習紀錄。

## 讀取與連線

每個程序依 DSN 重用 SQLAlchemy 連線池，基礎 5 條、臨時最多再加 10 條；交易與 Session 仍按操作建立。鎖等待 5 秒、SQL 60 秒的設定使用 SET LOCAL，交易結束後恢復；借出前檢查連線。隔離測試刪除自己的 DB 前先釋放該 DB 的池。

Podcast 與補充搜尋的唯讀操作使用教材共用鎖，追加、取消及其他修改仍使用排他鎖。搜尋紀錄清單只讀摘要，候選、授權與取得資訊由單筆讀取載入。知識結構讀取仍核對內容、run binding 及來源關係，不掃描原始 PDF，也不以跳過核對或公共快取換取速度。

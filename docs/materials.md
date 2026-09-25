# 教材處理

[文件入口](../README.md) · [使用指南](usage.md) · [系統架構](architecture.md)

## 來源與轉檔

每份教材是一個具名來源集合，可以包含一份或多份文件。單檔與轉換後 PDF 上限皆為 100 MiB；實際可用格式由 /v1/source-capabilities 宣告。

| 格式 | 處理方式 | 來源定位 |
| --- | --- | --- |
| PDF | 驗證原檔並建立預覽／mapping | 原頁碼 |
| DOCX | LibreOffice Writer 轉 PDF | 轉換後頁碼，段落對照可能有歧義 |
| PPTX | LibreOffice Impress 轉 PDF，不匯出 hidden slides／notes | 原投影片編號 |
| DOC／PPT | 檢查二進位 Office 容器，再轉 PDF | 轉換後頁碼，不推造原始段落或投影片定位 |
| UTF-8 TXT | 有界換行後排版 | 原始行號與轉換後頁碼 |
| Markdown | 限制 HTML 排版；raw HTML 顯示為文字，圖片不載入 | 原始 block 與轉換後頁碼 |

原檔、normalized PDF、mapping 各自保存身分與 hash。原檔下載使用 attachment 與原 MIME；PDF 預覽是獨立入口，不允許交換 artifact 類型。

所有格式解析在無網路的 bubblewrap 子程序中執行，唯讀掛載 runtime、renderer 與輸入，僅輸出與暫存位置可寫。時間、記憶體與檔案限制，以及拒絕的 Office 主動內容，由 [converter.py](../backend/src/document_normalization/converter.py) 和 [renderer.py](../backend/src/document_normalization/renderer.py) 定義。版本不符或轉換失敗會回報錯誤，不把未驗證檔案標為 ready。

## 草稿到來源快照

1. 建立教材草稿，保存名稱及建立意圖。
2. 各檔案以獨立 Idempotency-Key 上傳，保存原檔並排入 normalization。
3. 來源全部就緒後，由使用者確認要分析的來源與順序。
4. 後端於同一交易封存 SourceSet、bundle manifest 與 processing run。
5. Worker 依封存輸入處理；之後上傳的來源不會自動混入。

同一意圖重播不重複建立；變更來源內容、順序或 base revision 會核對並拒絕衝突。已封存的來源不可變；尚未被使用的 staged source 可由 owner 明確移除。

## Evidence 與分析

PDF 原生文字足夠時建立 native Evidence；未被原生文字涵蓋的重要圖片區域交由 Unlimited-OCR。混合頁面保留原生文字，並補入圖片區域的 OCR 結果。未能恢復的內容留下複核提示。

Evidence 維持教材、1-based 頁碼、區塊與定位的關係，保留程式碼、運算子、數字及技術字面值。換行合併依幾何與段落結構判斷，不跨欄或任意串接條列。

語意請求按章節與 Evidence 分批，以實際 tokenizer 檢查完整 prompt、目前概念目錄與輸出預算。模型引用整個 Evidence handle；程式還原正式來源關係，並驗證 Claim、Relation 與完整 Path。預算以 runtime lock 為準，不在文件重抄數值。

## 發布前檢核

初始分析與追加來源的結果，在發布前執行教材檢核。每批提供來源 Evidence 及概念，模型可提出重點整合、案例歸屬、別名、文字與關係修正。

程式核對覆蓋、來源歸屬、必要先備關係與字面值。無法支持的修改保留原文或拒絕發布，不靠補造來源取得通過。覆蓋／歸屬錯誤可進行一次有界補正；其他失敗如實回報。

已發布教材也可透過 /v1/materials/{material_id}/review 建立只檢核的新版本，不重跑 OCR 或初始分析；目前尚無獨立 UI 按鈕。模型提案與中間投影不直接作為正式 KS。

## 追加與版本

追加工作只對新增來源執行 extraction／語意分析，重用已驗證的既有內容，再檢核整合結果。發布時同一交易更新 KS、run output binding 與教材 head。

有可用新增內容的 partial／needs_review 結果可以發布，品質提示保留。沒有可用新增內容、來源錯誤、取消或處理失敗時保留目前 head。

地圖以目前 head 為入口；學習紀錄依 exact revision 恢復。被學習或活動工作引用的結構保留，無引用的舊完整結構可清理。來源檔案跨版本重用，不為每次更新另存合併 PDF。

## 失敗接續與 checkpoint

分析批次保存 Evidence、累積狀態、精確游標及執行快照。重試核對同 owner、教材、輸入與教材分析設定；已完成批次可重用，尚未完成部分才新增模型請求。

只剩確定性的建構／發布步驟時，不必重做模型推論。完全沒有可用新增 Claim 的失敗可重用 Evidence，但仍需要重試語意。checkpoint 損毀或設定不一致必須停止，不靜默全量重跑。

Checkpoint 依 DB 已提交的發布結果清理，partial／needs_review 也適用。尚未發布或不同輸入的失敗資料保留；清理失敗由 worker 補做，晚到 worker 不得重建已結束工作的 checkpoint。

私人 analysis archive 保存請求、回應、失敗與結果對照。重用的回應與本次新增呼叫分開計數，不能把歷史工作量冒稱本次費用。

## 取消、刪除與檔案恢復

取消一次更新保留教材、已發布地圖與學習。刪除整份教材則先保存刪除意圖，阻止新工作與晚到發布，再清理來源、分析、題組、答案及學習資料。

來源檔先移至 quarantine；DB 回滾時還原，commit 後移除。清理器跳過尚未提交的寫入／刪除，失敗可補做，不能影響其他教材或使用者。取消並不保證已送出的外部模型計算立即停止。

教材改名只修改顯示名稱，與刪除使用相同教材鎖序；不改來源身分、hash 或學習紀錄。

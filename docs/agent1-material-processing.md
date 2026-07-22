# Agent 1：教材處理

Agent 1 將已授權的本機 PDF 逐頁讀取，保留每個文字區塊的來源，再提供兩種後續使用方式：分析 PDF 原生結構，或建立可追溯的初步斷詞候選。

`已授權 PDF → 逐頁解析 → Material Blocks → (原生結構分析／初步斷詞候選)`

## 處理流程

| 步驟 | 白話說明 | 主要輸出 |
| --- | --- | --- |
| 教材解析 | 先確認 PDF、SHA-256（內容指紋）與頁數，再由 PyMuPDF 逐頁擷取文字 | 文字、頁碼、來源、解析狀態與失敗原因 |
| 原生結構分析 | 從 PDF 本身的多種結構資料互相比對，例如 `rawdict`、`words`、`blocks`、顯示圖片（displayed images）、繪圖資料（drawings）與 `find_tables`，並與既有文字區塊基準比較 | 教材、區塊與頁碼識別資訊（identity）、位置範圍（bbox）、來源紀錄（provenance）、統計摘要（bounded summaries）、與既有文字的比較結果（comparability）、文字閱讀順序（reading order）、辨識缺口與分類（gap signals/class）、處理狀態與原因（status/reasons），以及後續工具是否值得嘗試的訊號（eligibility） |
| 候選詞擷取 | 對成功區塊使用固定的 `jieba 0.42.1 + dict.txt.big + HMM=False` 設定切分文字 | 候選文字、文字位置（offset）、可回查證據（Evidence）、計數與涵蓋率（accounting/coverage） |

## 責任邊界

| 模組 | 保留內容 | 不代表或不執行 |
| --- | --- | --- |
| `material_blocks` | 逐頁文字區塊、頁面位置、來源參照、解析狀態與失敗原因 | 不負責關鍵字、Concept 或跨文件語意判斷 |
| `material_native_analysis` | 統計摘要、來源與位置資訊，以及可辨識的缺口、狀態與原因；只保留彙總結果（aggregate-only） | 不保存教材全文、原始資料封包（raw payload）、圖片位元組（image bytes）、繪圖指令（drawing commands）或表格儲存格內容（table cell content）；後續處理資格訊號（eligibility）不會作為啟動 `candidate_extraction` 的門檻，也不會自動呼叫它或執行其他工具 |
| `candidate_extraction` | 依原始順序切分文字，保留文字位置（offset）與可回查證據（Evidence），並確認候選可重建原文 | 候選不等同正式關鍵字或 Concept，不進行語意重要性判斷 |

## 測試重點

| 模組 | 驗證項目 |
| --- | --- |
| `material_blocks` | 逐頁擷取、輸入指紋（fingerprint）、頁數、空白或不可讀頁面、解析失敗、文字正規化與來源保留 |
| `material_native_analysis` | 比較字元、單字、區塊、圖片、繪圖與表格資料；驗證每頁的識別資訊、位置、來源、文字比較、閱讀順序、缺口分類與處理狀態；也檢查套件功能不支援、分析失敗、頁數不符與位置超界 |
| `candidate_extraction` | 繁體技術詞、固定斷詞設定、文字位置與可回查證據、空白與標點重建、排除紀錄、詞典來源與篡改阻擋、計數與涵蓋率 |

測試使用合成測試資料（synthetic fixture）與真實斷詞器，不包含私人教材或固定對照資料（Golden）內容。

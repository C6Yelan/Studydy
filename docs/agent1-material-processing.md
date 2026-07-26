# Agent 1：教材處理

Agent 1 將已授權的本機 PDF 逐頁讀取，保留每個文字區塊的來源，供原生結構分析與後續正規化流程使用。

`已授權 PDF → 逐頁解析 → Material Blocks → 原生 blocks 摘要 → normalized Material Blocks → 文字查找索引`

## 處理流程

| 步驟 | 白話說明 | 主要輸出 |
| --- | --- | --- |
| 教材解析 | 先確認 PDF、SHA-256（內容指紋）與頁數，再由 PyMuPDF 逐頁擷取文字 | 文字、頁碼、來源、解析狀態與失敗原因 |
| 原生結構分析 | 目前只使用 `page.get_text('blocks', sort=True)` 取得文字區塊結構摘要 | 分析 artifact 保留 identity、page_bbox、minimal provenance、blocks summary、status 與 reasons；由 `persist_material_native_analysis` 寫入 stable JSON |
| 正規化 | 接收 Material Blocks 與 native analysis，依 identity 配對，保留可用文字、來源、狀態、原因與 warning | 可回溯的 normalized Material Blocks 與對應 evidence |
| 文字查找索引 | 只處理已挑選的 normalized blocks；連續漢字取相鄰兩字，ASCII 技術名稱轉小寫並保留中間的 `.`, `_`, `-` | 查找鍵、來源、狀態與省略紀錄 |

## 責任邊界

| 模組 | 保留內容 |
| --- | --- |
| `material_blocks` | 逐頁文字區塊、頁面位置、來源參照、解析狀態與失敗原因 |
| `material_native_analysis` | blocks 的可用性、數量、類型與 bbox 摘要，以及 minimal provenance、狀態與原因；只保留彙總結果（aggregate-only），不保存教材全文或原始頁面內容 |
| `material_lexical_index` | 只協助按文字表面查找，不做語意判斷、排名或證據授權；索引輸出不保存教材全文 |

## 如何解讀狀態

狀態、原生結構摘要與失敗原因的完整解讀，請見[原生結構分析說明](agent1-material-native-analysis.md)。

## 測試重點

| 模組 | 驗證項目 |
| --- | --- |
| `material_blocks` | 逐頁擷取、輸入指紋（fingerprint）、頁數、空白或不可讀頁面、解析失敗、文字正規化與來源保留 |
| `material_native_analysis` | 驗證每頁的 identity、page_bbox、minimal provenance、blocks 可用性／數量／類型／bbox 摘要、處理狀態與原因；也檢查 `blocks_analysis_failed`、文件／頁面／來源參照與 bbox 原因 |
| normalized blocks | 來源定位、狀態／原因、warning／failure 與 deterministic evidence |
| lexical index | 查找鍵規則、固定排序、來源追蹤、狀態／省略紀錄，以及輸出不含教材全文 |

測試使用合成測試資料（synthetic fixture）驗證現行流程與契約，不包含私人教材或固定對照資料（Golden）內容。

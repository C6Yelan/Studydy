# Agent 1：教材處理

Agent 1 將已授權的本機 PDF 轉成可追溯的文字區塊，再建立保留原文位置的初步斷詞候選。

`已授權 PDF → 逐頁解析 → Material Blocks → 原生結構分析 → 初步斷詞候選`

## 處理流程

| 步驟 | 實作方式 | 主要輸出 |
| --- | --- | --- |
| 教材解析 | 驗證 PDF、SHA-256 與頁數，再由 PyMuPDF 逐頁擷取文字 | 文字、頁碼、來源、解析狀態與失敗原因 |
| 原生結構分析 | 以 PyMuPDF 的 `rawdict`、`words`、`blocks`、displayed images、drawings 與 `find_tables` 分析原生結構，並與既有 block 基準比較 | 逐頁 identity、bbox/provenance、bounded summaries、comparability、reading order、gap signals/class、status/reasons 與後續 eligibility |
| 候選詞擷取 | 對成功區塊使用 `jieba 0.42.1 + dict.txt.big + HMM=False` | 候選文字、offset、Evidence、accounting 與 coverage |

斷詞結果必須依原始順序完整重建區塊。這些候選只表示可追蹤的文字切分結果，不等同正式關鍵字或 Concept。

原生結構分析只保留 aggregate-only 的摘要與 provenance/bbox 等可追溯資訊，不保存教材全文、raw payload、圖片 bytes、drawing commands 或 table cell content。後續 eligibility 只表示有 evidence 支持的 routing 訊號，不會自動安裝、執行或採用額外工具。

## 測試重點

| 模組 | 驗證項目 |
| --- | --- |
| `material_blocks` | 逐頁擷取、輸入 fingerprint、頁數、空白或不可讀頁面、解析失敗、文字正規化與來源保留 |
| `material_native_analysis` | `rawdict`、words、blocks、displayed images、drawings、`find_tables` 的原生結構分析；逐頁 identity、bbox/provenance、bounded summary、comparability、reading order、gap class、status/reason 與後續 eligibility；API 不支援、分析失敗、頁數不符與 bbox 邊界 |
| `candidate_extraction` | 繁體技術詞、固定斷詞設定、offset/Evidence、空白與標點重建、排除紀錄、詞典來源與篡改阻擋 |

測試使用 synthetic fixture 與真實斷詞器，不包含私人教材或 Golden 內容。

# Agent 1：教材處理

Agent 1 將已授權的本機 PDF 轉成可追溯的文字區塊，再建立保留原文位置的初步斷詞候選。

`已授權 PDF → 逐頁解析 → Material Blocks → 初步斷詞候選 → 格式雜訊過濾與詞頻清單`

## 處理流程

| 步驟 | 實作方式 | 主要輸出 |
| --- | --- | --- |
| 教材解析 | 驗證 PDF、SHA-256 與頁數，再由 PyMuPDF 逐頁擷取文字 | 文字、頁碼、來源、解析狀態與失敗原因 |
| 候選詞擷取 | 對成功區塊使用 `jieba 0.42.1 + dict.txt.big + HMM=False` | 候選文字、offset、Evidence、accounting 與 coverage |
| 候選後處理 | 以 pure-Python 規則略過純空白／純標點／純符號候選，按完全相同文字累計次數並保留 Evidence 順序，再以固定規則排序 | `word`、`occurrence_count`、`evidence_refs` |

斷詞結果必須依原始順序完整重建區塊。這些候選只表示可追蹤的文字切分結果，不等同正式關鍵字或 Concept。
詞頻只表示候選文字的出現次數，不代表關鍵字、Concept 或語意重要性。

## 測試重點

| 模組 | 驗證項目 |
| --- | --- |
| `material_blocks` | 逐頁擷取、輸入 fingerprint、頁數、空白或不可讀頁面、解析失敗、文字正規化與來源保留 |
| `candidate_extraction` | 繁體技術詞、固定斷詞設定、offset/Evidence、空白與標點重建、排除紀錄、詞典來源與篡改阻擋 |
| `candidate_postprocessing` | 過濾格式雜訊、聚合相同文字與 `occurrence_count`、保留 `evidence_refs` 順序、deterministic 排序、input immutability，以及 extractor-to-postprocessor synthetic integration |

測試使用 synthetic fixture 與真實斷詞器，不包含私人教材或 Golden 內容。

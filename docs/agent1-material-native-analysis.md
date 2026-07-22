# PDF 原生結構分析

這份說明補充 Agent 1 中「原生結構分析」的實際責任。它分析 PDF 的結構證據，幫助讀者知道文字、圖片、繪圖或表格是否值得進一步查證；它不是品質分數，也不會自動啟動其他工具。

## 整體流程

公開入口是 `analyze_material_native`。它先確認輸入 Material Blocks 的 schema 與 materials 格式，再依教材和既有 block 的頁面位置找到對應 PDF。每一頁依序執行以下步驟：

1. 開啟對應頁面；如果頁面無法讀取，保留帶有來源識別與失敗原因的 `failed` row。
2. 用 PyMuPDF 取得原生結構資料，並只在各自的外部呼叫範圍處理 API 不支援或分析失敗。
3. 將各來源整理成統計摘要與 bbox（頁面物件的位置矩形），不把原始內容放入結果。
4. 把原生摘要與既有 Material Block 的文字基準比較，判斷文字是否可比及閱讀順序是否一致。
5. 依文字、圖片、繪圖與表格證據整理 gap signals，再產生 `gap_class` 與 `next_task_eligibility`。
6. 回傳逐頁結果，最外層使用 `material-native-analysis/v1`、頁數與排序後的 pages；整體結果仍是 aggregate-only 的可追溯摘要。

輸入的 PDF 路徑只由已授權的本機對照資料提供；這個入口不是使用者上傳 API。

## 單頁分析從哪裡取得證據

| 來源 | 提供的證據 | 文件中的限制 |
| --- | --- | --- |
| `rawdict` | 字元、span、文字 block 的數量、文字摘要與 bbox；可確認原生文字是否存在 | 只保存統計與有限摘要，不保存 rawdict 本身 |
| `words` | 逐字順序、文字摘要與 bbox；主要用來比較閱讀順序 | 不保存完整 words 陣列 |
| `blocks` | 文字與圖片 block 的數量、摘要與 bbox | 不保存完整 block payload |
| 顯示圖片（`displayed images`） | 圖片數量、bbox、頁面覆蓋比例，以及是否像滿版背景 | 不保存圖片 bytes |
| 繪圖資料（`drawings`） | 向量繪圖數量、bbox 與頁面覆蓋比例 | 不保存 drawing commands |
| 表格（`find_tables`） | 表格數量與表格 bbox | 不保存 table cell content |

所有 bbox 都會檢查是否為有效、有面積的矩形，以及是否在頁面允許範圍內。`native_bbox_invalid` 表示矩形本身不可用；`native_bbox_outside_page_tolerance` 表示超出頁面邊界的容許誤差。後者是非阻斷原因，不能單獨把結果改成 `partial`。

## 基準比較與閱讀順序

既有 Material Block 提供文字基準。分析會分別比較 `rawdict` 與 `blocks` 的文字摘要，也比較去除空白後的 `words` 文字。這些比較只回答「原生資料是否與既有 block 可比」，不判斷內容是否正確或有語意價值。

`reading_order.assessment` 有四種值：

| 值 | 意義 |
| --- | --- |
| `match` | `words` 的非空白文字順序與基準一致。 |
| `divergent` | `words` 的非空白文字順序與基準不同。 |
| `not_comparable` | 基準分析不是成功狀態，或基準文字為空，沒有可比資料。 |
| `unavailable` | 無法取得可用的 words 順序比較；會保留 `word_order_comparison_unavailable` 等 reason。 |

如果教材頁數與既有 blocks 數量不同，仍會保留可分析頁面的結果，但該頁狀態會標為 `partial` 並附上頁數不符原因。

## 缺口與 routing

正常單頁分析的 `gap_class` 有四種：`no_detected_native_gap`（沒有偵測到缺口）、`ordinary_layout_gap`（一般版面需要查證）、`complex_structure_gap`（複雜結構需要查證）與 `scan_image_text_gap`（可能只有圖片而沒有可用原生文字）。如果頁面根本無法分析，失敗 row 會使用 `analysis_unavailable`。

`next_task_eligibility` 只有三個布林旗標：

- `task5_ordinary_layout`：文字基準存在，但閱讀順序出現差異。
- `task6_scan_image_text`：基準與原生文字都沒有內容，且顯示圖片覆蓋面積達到掃描判定條件。
- `task7_complex_structure`：存在表格、非背景圖片或密集向量等結構證據，且閱讀順序出現差異。

這三個旗標是 routing heuristic（分流啟發式），不是品質分數、通過門檻或工具選擇。`eligibility` 只表示值得後續查證，不會作為啟動 `candidate_extraction` 的門檻，也不會自動呼叫 OCR、layout 或其他工具。
`task5_ordinary_layout` 與 `task7_complex_structure` 可以同時為 true；`task6_scan_image_text` 不會與兩者同時成立。`gap_class` 只保留一個值，固定優先序是 `scan_image_text_gap` > `complex_structure_gap` > `ordinary_layout_gap` > `no_detected_native_gap`。

## 六個數值 heuristic

目前程式使用六個固定數值來協助分流。它們是可重現的規則，不代表最佳值，也不代表品質評分：

| 規則 | 數值 | 用途 |
| --- | ---: | --- |
| bbox 頁面容許誤差 | `0.5` points | 判斷 bbox 是否仍在頁面邊界附近。 |
| 掃描圖片覆蓋比例 | `0.5` | 基準與原生文字為空時，判斷圖片是否足以支持掃描文字查證訊號。 |
| 複雜結構的非背景圖片比例 | `0.10` | 判斷非背景圖片幾何是否足以成為結構證據。 |
| 複雜結構的向量覆蓋比例 | `0.10` | 搭配向量數量判斷是否存在密集向量幾何。 |
| 複雜結構的向量數量 | `8` | 搭配向量覆蓋比例判斷密集向量幾何。 |
| 滿版背景圖片比例 | `0.90` | 將接近整頁的圖片區分為背景，避免把它當作非背景結構證據。 |

## 狀態與錯誤

`status` 有三種主要值：`success` 表示分析結果可用，但仍可能有 reason 或 gap；`partial` 表示部分分析不可用或有明確限制；`failed` 表示無法產生可用的單頁結果。`partial` 不等於需要 OCR，`success` 也不代表沒有缺口。

常見外部呼叫原因包括 `rawdict_api_unsupported`、`words_api_unsupported`、`blocks_api_unsupported`、`displayed_image_api_unsupported`、`drawing_api_unsupported` 與 `table_api_unsupported`，以及相對應的 analysis failed 原因。頁面無法讀取時會保留 `page_unreadable`；頁面 bbox 不可用時會保留 `page_bbox_invalid`。這些 reason 是診斷資訊，不會偷偷改用另一種來源猜測結果。

## 資料保留與不負責事項

結果保留教材、block、頁碼與來源識別資訊、PyMuPDF provenance、bbox、統計摘要、比較結果、reading order、gap signals、status、reasons 與 eligibility。結果不保存教材全文、原始 rawdict/words/blocks payload、圖片 bytes、drawing commands、table cell content、tokens、candidate、Concept 或語意欄位。

本分析不建立關鍵字、Concept、摘要、Relation，也不改變既有 Material Blocks 或 candidate extraction。它只提供結構證據與後續查證的 routing 訊號；後續工具或更高階語意處理不在這份文件的已實作範圍內。

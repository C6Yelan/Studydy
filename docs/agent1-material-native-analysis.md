# PDF 原生結構分析

這份說明補充 Agent 1 中「原生結構分析」的實際責任。目前流程只讀取 PyMuPDF blocks 結構摘要。

## 整體流程

公開入口是 `analyze_material_native`。它先確認輸入 Material Blocks 的 schema 與 materials 格式，再依教材和既有 block 的頁面位置找到對應 PDF。每一頁依序執行以下步驟：

1. 開啟對應頁面；如果頁面無法讀取，保留帶有來源識別與失敗原因的 `failed` row。
2. 只用 `page.get_text('blocks', sort=True)` 取得文字區塊。
3. 將 blocks 整理成可用性、數量、類型與 bbox（頁面物件的位置矩形）摘要。
4. 回傳逐頁 native row，保留 identity、page_bbox、minimal provenance、blocks summary、status 與 reasons；`analyze_material_native` 只產生分析 artifact，`persist_material_native_analysis` 才將它寫入 stable JSON。

輸入的 PDF 路徑由已授權的本機對照資料提供。

所有 bbox 都會檢查是否為有效、有面積的矩形，以及是否在頁面允許範圍內。`native_bbox_invalid` 表示矩形本身不可用；`native_bbox_outside_page_tolerance` 表示超出頁面邊界的容許誤差，屬於非阻斷原因。

## Native row 與狀態

每列保留 identity、`page_bbox`、minimal `provenance`、blocks summary、`status` 與 `reasons`。分析 artifact 使用 `material-native-analysis/v1`；`persist_material_native_analysis` 將它寫入 `.studydy-runtime/materials/native-analysis/stable/material-native-analysis.v1.json`。blocks summary 只描述 blocks 的可用性、數量、類型與 bbox。

如果教材頁數與既有 blocks 數量不同，仍會保留可分析頁面的結果，但該頁狀態會標為 `partial` 並附上頁數不符原因。

## 狀態與錯誤

`status` 有三種主要值：`success` 表示分析結果可用，但仍可能有 reason；`partial` 表示部分分析不可用或有明確限制；`failed` 表示無法產生可用的單頁結果。

blocks-only 流程使用 `blocks_analysis_failed`，並保留既有的 document、page、source-ref 與 bbox reasons，例如 `page_unreadable` 與 `page_bbox_invalid`。Reasons 會隨 native row 保留，供結果判讀。

## 資料保留範圍

結果保留教材、block、頁碼與來源識別資訊、minimal provenance、page_bbox、blocks summary、status 與 reasons；教材全文與原始頁面內容不進入結果。

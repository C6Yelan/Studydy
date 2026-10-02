# 觀念圖卡（F4）

## Phase A

基線 dev：`3e3bfcfcf2616d86fca3d687a228b532262990f5`。
相較規劃基準 `76dfa532`，新增的是部署／HTTPS 設定，KS authority 不變。

`F4_BASIC_PHASE_A_PASS`

- `backend/tests/test_concept_cards.py`：6 passed。合成資料沿用正式 KS builder／validator，涵蓋多 claims、prerequisite、contrast、無關概念、程式碼／公式、needs_review 與排除頁。
- 每個 claim 與 relation 引用仍指向 canonical Evidence；方向、literal 與品質逐項斷言，不使用畫面 snapshot 代替。
- 本機 Chromium 最小樣張於 1440px／390px 驗證文字 reflow、程式碼局部水平捲動、完整文字不變。這是版面可行性證據，完整產品互動另驗。
- 現有發布驗證要求 relation evidence 屬於端點概念；可直接使用既有 Evidence source resolver。
- API 教材 UUID 與 KS 內容 material hash 是不同 identity，selection 必須同時保留。

基本模板限定單卡／多卡並列及既有關係文字，不生成定義、比較軸或摘要。
自動選材的排序／數量尚無核定規則，manual slice 不依賴此決策。

# 概念卡離線圖庫

正式前端使用 `frontend/src/features/concept-cards/icons/` 的產物，沒有 Python／SQLite 執行期服務。
Tabler 3.49.0 的 5,184 個 outline icons 全數納入；採用 PoC 的 lexical 路徑，BM25 在實際 89 概念上未增加覆蓋，不帶入正式產品。

更新時下載官方 `@tabler/icons` package 並解壓，以及 CC-CEDICT UTF-8 詞典；不使用私人教材建置索引：

```sh
python3 ops/icons/build_catalog.py --tabler /path/to/tabler-package --cedict /path/to/cedict.txt
```

腳本僅使用 Python 標準庫，輸入本機公開素材，不自行下載。相同輸入可重建相同輸出。
目前輸入版本與 SHA-256 記錄在 `sources.json`；CC-CEDICT 會更新，更新者須核對新的差異，不能將新快照冒充舊檔。
`catalog.json` 保留別名出處和詞義限定，中文詞表用於最長詞切分，避免在複合詞內任意抽字。
`shapes.json` 僅含經白名單檢查的 SVG 幾何，下載的 SVG 直接嵌入幾何，不依賴遠端圖片。

詞彙選圖要求 Concept／Claim 與同一 Claim 的有效 Evidence 命中同一物件，再處理否定句與已知多義詞。
圖示為概念或教材提及物件的閱讀標記，不取代關係來源；公式與程式碼仍優先保留原文。
來源、待複核、缺漏標記在下方詳細內容保留；獨立 SVG 的 metadata 也保留来源與狀態。

驗證入口：`npm --prefix frontend test`、`npm --prefix frontend run build`，以及 `frontend/e2e/mock/concept-cards.spec.ts`。
授權見 `THIRD_PARTY_CONTENT.md` 和網站 `/licenses/concept-icons.txt`。
